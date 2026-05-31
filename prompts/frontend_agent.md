PROJECT CONTEXT:
- Frontend stack: {frontend_stack}
- Design system: {design_tokens}

TASK: {task_id} — {title}

DESCRIPTION:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

FILES TO CREATE:
{files_to_create}

API CONTRACTS TO CONSUME:
{api_contracts}

RULES:
1. All API calls must use the exact endpoints and request/response shapes in API CONTRACTS.
2. Handle loading, error, and empty states for every async operation.
3. No hardcoded strings visible to users — use a constants file.
4. Components must be accessible: semantic HTML, ARIA labels where needed.
5. Write an integration test for every page/view.
6. At the end, output a JSON summary: {"files_created": [], "tests_written": [], "notes": ""}
