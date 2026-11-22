-- Add explicit delivery state without reclassifying historical digests as pending.
-- Safe to run repeatedly.

alter table digests add column if not exists is_breaking boolean not null default false;
alter table digests add column if not exists delivery_status text;
alter table digests add column if not exists delivered_at timestamptz;
alter table digests add column if not exists delivery_error text;

-- Rows created before this migration were sent by the legacy pipeline.
update digests
set delivery_status = 'sent',
    delivered_at = coalesce(delivered_at, sent_at)
where delivery_status is null;

alter table digests alter column delivery_status set default 'pending';
alter table digests alter column delivery_status set not null;

alter table digests drop constraint if exists digests_delivery_status_check;
alter table digests add constraint digests_delivery_status_check
  check (delivery_status in ('pending', 'sending', 'sent', 'failed', 'superseded'));

create index if not exists idx_digests_delivery
  on digests(user_id, delivery_status, sent_at desc);
