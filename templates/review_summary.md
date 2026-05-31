# Review Summary — {{ project_name }}

**Project ID**: `{{ project_id }}`
**Overall status**: **{{ overall_status }}**
**Merge ready**: {{ merge_ready }}

---

## PRD coverage
- Covered: {{ prd_coverage.coverage_percent }}%
- Requirements covered: {{ prd_coverage.requirements_covered | join(", ") }}
{% if prd_coverage.requirements_missing %}
- **Missing**: {{ prd_coverage.requirements_missing | join(", ") }}
{% endif %}

## Gate summary
| Gate | Status |
|---|---|
| Tests passed | {{ "✅" if gate_summary.tests_passed else "❌" }} |
| Security passed | {{ "✅" if gate_summary.security_passed else "❌" }} |
| Quality passed | {{ "✅" if gate_summary.quality_passed else "❌" }} |
| All files present | {{ "✅" if gate_summary.all_files_present else "❌" }} |

{% if flags_for_human %}
## Decisions required
{% for flag in flags_for_human %}
### {{ flag.flag_type }}
{{ flag.description }}

**Options:**
{% for opt in flag.options %}
- {{ opt }}{% if opt == flag.recommended_option %} *(recommended)*{% endif %}
{% endfor %}
{% endfor %}
{% else %}
## Decisions required
*None — auto-approved.*

> {{ auto_approval_rationale }}
{% endif %}
