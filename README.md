# Solace Scoop

**Agent Mesh Account Intelligence — powered by Solace Agent Mesh.**

A live demo of event-driven AI agents working in parallel to research any company. Five specialized agents (People Intel, Corporate Intel, Market & Competitive, Risk & Compliance, Hiring & Growth) each publish their findings via Solace PubSub+, then a Strategy Orchestrator synthesizes everything into a unified intelligence brief.

**[Try it live](https://raphael-solace.github.io/solace-scoop/)** — free, no signup.

---

## How it works

1. Enter a company name (and optional context about what you sell)
2. Five AI agents launch in parallel via Solace Event Mesh
3. Each agent researches a different dimension (people, financials, market, risks, hiring)
4. A Strategy Orchestrator synthesizes all findings into an actionable brief
5. Results display in real time as each agent completes

## Architecture

```
User Input
    |
    v
[Solace Event Mesh / PubSub+]
    |
    +---> People Intel Agent --------+
    +---> Corporate Intel Agent -----+
    +---> Market & Competitive Agent-+--> Strategy Orchestrator --> Brief
    +---> Risk & Compliance Agent ---+
    +---> Hiring & Growth Agent -----+
```

Each agent publishes events to the mesh as it completes its research. The orchestrator subscribes to all agent completion events and synthesizes the final brief once all agents have reported.

## Tech Stack

- **Frontend**: Vanilla HTML/CSS/JS (zero dependencies)
- **AI**: Perplexity API (`sonar` model) for live web research
- **Design**: Solace brand (Instrument Serif + Plus Jakarta Sans, Solace green `#00C895`)
- **Hosting**: GitHub Pages (static, free)

## License

MIT
