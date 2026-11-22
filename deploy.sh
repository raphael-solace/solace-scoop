#!/bin/bash
set -euo pipefail

REGION="eu-central-1"
APP_NAME="scoop"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODE="${1:-deploy}"
ADMIN_SECRET_NAME="$APP_NAME/admin"

provision_admin() {
  local credential_file
  credential_file="${ADMIN_CREDENTIAL_FILE:-$HOME/.scoop-admin-credentials-$(date -u +%Y%m%dT%H%M%SZ)}"

  printf '=== Provisioning Scoop admin credentials (%s) ===\n' "$REGION"
  terraform -chdir="$SCRIPT_DIR/infra" init -input=false
  terraform -chdir="$SCRIPT_DIR/infra" validate -no-color
  terraform -chdir="$SCRIPT_DIR/infra" apply -auto-approve -input=false \
    -target=aws_secretsmanager_secret.admin_auth

  local version_count
  version_count=$(aws secretsmanager list-secret-version-ids --secret-id "$ADMIN_SECRET_NAME" \
    --region "$REGION" --query 'length(Versions)' --output text)
  if [ "$version_count" != "0" ]; then
    printf 'Admin secret already has a version; refusing to replace it.\n' >&2
    exit 1
  fi

  # Secret JSON flows directly over stdin. Only the generated username/password
  # are persisted, in a mode-0600 file outside the repository by default.
  python3 "$SCRIPT_DIR/backend/generate_admin_credentials.py" \
    --credential-output "$credential_file" | \
    aws secretsmanager put-secret-value --secret-id "$ADMIN_SECRET_NAME" \
      --region "$REGION" --secret-string file:///dev/stdin \
      --query VersionId --output text >/dev/null

  printf '\nAdmin secret provisioned. One-time login saved to:\n%s\n' "$credential_file"
  printf 'Protect that file, run %s normally to deploy, then delete it.\n' "$0"
}

case "$MODE" in
  --provision-admin)
    [ "$#" -eq 1 ] || { printf 'Usage: %s --provision-admin\n' "$0" >&2; exit 2; }
    provision_admin
    exit 0
    ;;
  deploy)
    [ "$#" -eq 0 ] || { printf 'Usage: %s [--provision-admin]\n' "$0" >&2; exit 2; }
    ;;
  *)
    printf 'Usage: %s [--provision-admin]\n' "$0" >&2
    exit 2
    ;;
esac

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URL="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$APP_NAME"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)}"

printf '=== Deploying Scoop to AWS (%s) ===\n' "$REGION"
printf 'Account: %s\nImage tag: %s\n\n' "$ACCOUNT_ID" "$IMAGE_TAG"

printf '[1/6] Terraform validate/apply...\n'
terraform -chdir="$SCRIPT_DIR/infra" init -input=false
terraform -chdir="$SCRIPT_DIR/infra" validate -no-color
if [ "$(aws secretsmanager list-secret-version-ids --secret-id "$ADMIN_SECRET_NAME" \
  --region "$REGION" --query 'length(Versions)' --output text 2>/dev/null || printf 0)" = "0" ]; then
  printf 'Admin credentials are not provisioned. Run %s --provision-admin first.\n' "$0" >&2
  exit 1
fi
terraform -chdir="$SCRIPT_DIR/infra" apply -auto-approve -input=false
ECR_REPO=$(terraform -chdir="$SCRIPT_DIR/infra" output -raw ecr_repository_url)

printf '\n[2/6] Building immutable image...\n'
docker build --platform linux/amd64 --provenance=false -t "$ECR_REPO:$IMAGE_TAG" .

printf '\n[3/6] Pushing image...\n'
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
docker push "$ECR_REPO:$IMAGE_TAG"
IMAGE_DIGEST=$(aws ecr describe-images --repository-name "$APP_NAME" \
  --image-ids "imageTag=$IMAGE_TAG" --region "$REGION" \
  --query 'imageDetails[0].imageDigest' --output text)
IMAGE="$ECR_REPO@$IMAGE_DIGEST"

printf '\n[4/6] Registering pinned API/digest task definitions...\n'
for FAMILY in "$APP_NAME-api" "$APP_NAME-digest"; do
  aws ecs describe-task-definition --task-definition "$FAMILY" --region "$REGION" \
    --query taskDefinition > "/tmp/$FAMILY.json"
  python3 - "$FAMILY" "$IMAGE" <<'PY'
import json,sys
family,image=sys.argv[1:]
d=json.load(open(f"/tmp/{family}.json"))
for key in ['taskDefinitionArn','revision','status','requiresAttributes','compatibilities','registeredAt','registeredBy']:
    d.pop(key,None)
for container in d['containerDefinitions']:
    container['image']=image
open(f"/tmp/{family}-register.json","w").write(json.dumps(d))
PY
  aws ecs register-task-definition --region "$REGION" \
    --cli-input-json "file:///tmp/$FAMILY-register.json" \
    --query 'taskDefinition.taskDefinitionArn' --output text
 done

DIGEST_TASK=$(aws ecs describe-task-definition --task-definition "$APP_NAME-digest" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)

printf '\n[5/6] Running required database migrations...\n'
NETWORK=$(aws ecs describe-services --cluster "$APP_NAME" --services "$APP_NAME-api" \
  --region "$REGION" --query 'services[0].networkConfiguration.awsvpcConfiguration' --output json)
SUBNET=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["subnets"][0])' <<<"$NETWORK")
SG=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["securityGroups"][0])' <<<"$NETWORK")
MIGRATION_TASK=$(aws ecs run-task --cluster "$APP_NAME" --task-definition "$APP_NAME-digest" \
  --launch-type FARGATE --region "$REGION" \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"digest","command":["python","run_migrations.py"]}]}' \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$APP_NAME" --tasks "$MIGRATION_TASK" --region "$REGION"
MIGRATION_EXIT=$(aws ecs describe-tasks --cluster "$APP_NAME" --tasks "$MIGRATION_TASK" \
  --region "$REGION" --query 'tasks[0].containers[0].exitCode' --output text)
if [ "$MIGRATION_EXIT" != "0" ]; then
  echo "Migration failed; refusing service rollout" >&2
  exit 1
fi

printf '\n[6/6] Updating API service and scheduled targets...\n'
API_TASK=$(aws ecs describe-task-definition --task-definition "$APP_NAME-api" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)
DIGEST_TASK=$(aws ecs describe-task-definition --task-definition "$APP_NAME-digest" \
  --region "$REGION" --query 'taskDefinition.taskDefinitionArn' --output text)

# EventBridge role policies in this stack authorize an exact task revision.
ROLE_NAME="$APP_NAME-eventbridge"
POLICY_NAME="run-ecs-task"
aws iam get-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" \
  --query PolicyDocument --output json > /tmp/scoop-eventbridge-policy-live.json
python3 - "$DIGEST_TASK" <<'PY'
import json,sys
arn=sys.argv[1]
d=json.load(open('/tmp/scoop-eventbridge-policy-live.json'))
for statement in d['Statement']:
    actions=statement.get('Action',[])
    if isinstance(actions,str): actions=[actions]
    if 'ecs:RunTask' in actions:
        statement['Resource']=[arn]
open('/tmp/scoop-eventbridge-policy.json','w').write(json.dumps(d))
PY
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" \
  --policy-document file:///tmp/scoop-eventbridge-policy.json

for RULE in "$APP_NAME-research" "$APP_NAME-send" "$APP_NAME-brand-monitor" \
            "$APP_NAME-daily-scan" "$APP_NAME-source-refresh" \
            "$APP_NAME-company-pool-shadow" "$APP_NAME-recovery-sunday" \
            "$APP_NAME-recovery-monday"; do
  COUNT=$(aws events list-targets-by-rule --rule "$RULE" --region "$REGION" \
    --query 'length(Targets)' --output text 2>/dev/null || echo 0)
  [ "$COUNT" = "0" ] && continue
  aws events list-targets-by-rule --rule "$RULE" --region "$REGION" \
    --query 'Targets[0]' > "/tmp/$RULE-target.json"
  python3 - "$RULE" "$DIGEST_TASK" <<'PY'
import json,sys
rule,arn=sys.argv[1:]
p=f'/tmp/{rule}-target.json'
d=json.load(open(p)); d['EcsParameters']['TaskDefinitionArn']=arn
open(p,'w').write(json.dumps([d]))
PY
  aws events put-targets --rule "$RULE" --region "$REGION" \
    --targets "file:///tmp/$RULE-target.json" >/dev/null
done

aws ecs update-service --cluster "$APP_NAME" --service "$APP_NAME-api" \
  --task-definition "$API_TASK" --force-new-deployment \
  --region "$REGION" --no-cli-pager >/dev/null
aws ecs wait services-stable --cluster "$APP_NAME" --services "$APP_NAME-api" --region "$REGION"

printf '\n=== Deployed ===\nImage: %s\nAPI: %s\nDigest: %s\n' "$IMAGE" "$API_TASK" "$DIGEST_TASK"
