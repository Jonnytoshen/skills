# Skills For AI Agent

A collection of production-grade agent skills.

## Skills

| Skill | Description | Install |
|-------|-------------|---------|
| [**Writing Project Experience**](./skills/writing-project-experience/) | Generate evidence-backed project experience from a git repository | `npx skills add Jonnytoshen/skills --path skills/writing-project-experience` |
| [**Interview Preparation**](./skills/interview-preparation/) | Turn a job description and résumé into a JD breakdown, evidence cards, and practice-ready answers (optionally verified against the project repo) | `npx skills add Jonnytoshen/skills --path skills/interview-preparation` |

## Quick Start

Install any skill with:

```bash
npx skills add Jonnytoshen/skills --path skills/<skill-name>
```

Then invoke in your agent terminal:

```bash
/writing-project-experience <repo>         # Generate a project-experience entry from a git repo
/interview-preparation <JD> <resume>       # Build a JD × résumé interview prep pack (repo optional)
```

## License

MIT
