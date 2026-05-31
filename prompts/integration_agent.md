PROJECT CONTEXT:
- Backend stack: {backend_stack}

TASK: {task_id} — {title}

INTEGRATION TARGET: {service_name}
INTEGRATION TYPE: {integration_type}

DESCRIPTION:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

FILES TO CREATE:
{files_to_create}

RULES:
1. Use the official SDK if available. Do not use raw HTTP unless no SDK exists.
2. Implement webhook signature verification for all inbound webhooks.
3. All credentials via environment variables. List required env vars in your notes.
4. Implement idempotency for payment and order operations.
5. Write sandbox/test-mode tests — never use live credentials in tests.
6. Handle rate limits and service unavailability gracefully.
7. At the end, output a JSON summary: {"files_created": [], "tests_written": [], "env_vars_required": [], "notes": ""}
