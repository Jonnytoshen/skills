# Skills For AI Agent

A collection of production-grade agent skills.

## Skills

| Skill | Description | Install |
|-------|-------------|---------|
| [**Writing Project Experience**](./skills/writing-project-experience/) | Generate evidence-backed project experience from a git repository | `npx skills add Jonnytoshen/skills --path skills/writing-project-experience` |
| [**JD Decoder**](./skills/jd-decoder/) | Decode a job description into the real hiring problem behind it: ranked requirements, interviewer focus, and ambiguities worth clarifying (JD only) | `npx skills add Jonnytoshen/skills --path skills/jd-decoder` |
| [**Interview Preparation**](./skills/interview-preparation/) | Turn a job description and résumé into a JD breakdown, evidence cards, and practice-ready answers (optionally verified against the project repo) | `npx skills add Jonnytoshen/skills --path skills/interview-preparation` |

Have only a JD? Start with **JD Decoder**. Have a JD plus your résumé (and optionally a repo)? Use **Interview Preparation**, which starts with the same JD breakdown.

## Quick Start

Install any skill with:

```bash
npx skills add Jonnytoshen/skills --path skills/<skill-name>
```

Then invoke in your agent terminal:

```bash
/writing-project-experience <repo>         # Generate a project-experience entry from a git repo
/jd-decoder <JD>                           # Decode what the role is really hiring for (JD only)
/interview-preparation <JD> <resume>       # Build a JD × résumé interview prep pack (repo optional)
```

## License

MIT
