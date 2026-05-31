PROJECT CONTEXT:
- Tech stack: {backend_stack}
- Database: {database}

TASK: {task_id} — {title}

DESCRIPTION:
{description}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

FILES TO CREATE:
{files_to_create}

API CONTRACTS TO IMPLEMENT:
{api_contracts}

DATABASE SCHEMA (relevant tables):
{schema_tables}

FOLDER STRUCTURE (your scope only):
{relevant_folder_structure}

RULES:
1. Implement every acceptance criterion. Do not skip any.
2. Every function must have a docstring.
3. Every API endpoint must have input validation and error handling.
4. Never hardcode secrets. Use environment variables.
5. Write a unit test for every function with business logic.
6. Output only the files listed in FILES TO CREATE. Do not create additional files.
7. At the end, output a JSON summary: {"files_created": [], "tests_written": [], "notes": ""}
