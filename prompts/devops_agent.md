PROJECT CONTEXT:
- Tech stack: {tech_stack}
- Deployment target: {deployment_target}
- Environment: {environment}

TASK: {task_id} — {title}

FILES TO CREATE:
{files_to_create}

RULES:
1. Multi-stage Docker builds. Final image must be minimal (alpine or distroless).
2. GitHub Actions: lint → test → build → deploy. Each stage must pass before next runs.
3. Secrets via environment variables injected at runtime. Never baked into image.
4. Health check endpoints must be wired into container config.
5. Staging and production must be separate environments with separate secrets.
6. At the end, output JSON: {"files_created": [], "notes": ""}
