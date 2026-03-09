from __future__ import annotations

from pathlib import Path
from typing import Dict, List


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_role_map(text: str) -> Dict[str, str]:
    role_map: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "R_" not in stripped:
            continue
        parts = [part.strip().strip("`") for part in stripped.strip("|").split("|")]
        if not parts:
            continue
        role_id = parts[0]
        if not role_id.startswith("R_"):
            continue
        spec_path = next(
            (part for part in parts if "role_spec_" in part and part.endswith(".md")),
            "",
        )
        if spec_path:
            role_map[role_id] = spec_path
    return role_map


def _missing_phrases(text: str, phrases: List[str]) -> List[str]:
    return [phrase for phrase in phrases if phrase not in text]


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", ".")))

    copilot_path = root / str(
        inputs.get("copilot_path", ".github/copilot-instructions.md")
    )
    agents_path = root / str(inputs.get("agents_path", "AGENTS.md"))
    security_path = root / str(inputs.get("security_path", "SECURITY.md"))
    risk_path = root / str(
        inputs.get("risk_register_path", "docs/compliance/risk_register.md")
    )
    fxcm_path = root / str(
        inputs.get("fxcm_license_path", "docs/compliance/fxcm-sdk-license-review.md")
    )
    ci_path = root / str(inputs.get("ci_path", ".github/workflows/ci.yml"))
    dependabot_path = root / str(
        inputs.get("dependabot_path", ".github/dependabot.yml")
    )
    ci_manifest_path = root / str(
        inputs.get("ci_manifest_path", "tools/exit_gates/manifest.ci.json")
    )

    missing_files = [
        str(path.relative_to(root)).replace("\\", "/")
        for path in [
            copilot_path,
            agents_path,
            security_path,
            risk_path,
            fxcm_path,
            ci_path,
            dependabot_path,
            ci_manifest_path,
        ]
        if not path.is_file()
    ]
    if missing_files:
        return {"ok": False, "details": "missing_files=" + ",".join(missing_files)}

    copilot_text = _read_text(copilot_path)
    agents_text = _read_text(agents_path)
    security_text = _read_text(security_path)
    risk_text = _read_text(risk_path)
    fxcm_text = _read_text(fxcm_path)
    ci_text = _read_text(ci_path)

    errors: List[str] = []

    copilot_roles = _extract_role_map(copilot_text)
    agents_roles = _extract_role_map(agents_text)
    if not copilot_roles:
        errors.append("copilot_roles_missing")
    if not agents_roles:
        errors.append("agents_roles_missing")
    if copilot_roles and agents_roles and copilot_roles != agents_roles:
        copilot_keys = set(copilot_roles)
        agents_keys = set(agents_roles)
        if copilot_keys != agents_keys:
            errors.append(
                "role_id_mismatch:copilot=%s:agents=%s"
                % (
                    ",".join(sorted(copilot_keys)),
                    ",".join(sorted(agents_keys)),
                )
            )
        else:
            diffs = [
                role_id
                for role_id in sorted(copilot_keys)
                if copilot_roles.get(role_id) != agents_roles.get(role_id)
            ]
            if diffs:
                errors.append("role_spec_mismatch:" + ",".join(diffs))

    copilot_missing = _missing_phrases(
        copilot_text,
        [
            "index-only mirror for discovery",
            "Do not redefine triggers/routing rules there.",
        ],
    )
    if copilot_missing:
        errors.append("copilot_missing=" + ",".join(copilot_missing))

    agents_missing = _missing_phrases(
        agents_text,
        [
            "Source of truth: .github/copilot-instructions.md",
            "Mirror mode: index-only",
            "Do not redefine triggers or precedence here.",
        ],
    )
    if agents_missing:
        errors.append("agents_missing=" + ",".join(agents_missing))

    security_missing = _missing_phrases(
        security_text,
        [
            "## Deployment Boundary",
            "## Automated Enforcement",
            "pip-audit",
            "bandit",
            "npm audit",
            "localhost only",
        ],
    )
    if security_missing:
        errors.append("security_missing=" + ",".join(security_missing))

    risk_missing = _missing_phrases(
        risk_text,
        [
            "### 1.8 Automated Enforcement",
            "### 1.9 Commercial Deployment Boundary",
            "written agreement with FXCM",
            "Accepted Risks",
            "scheduled review",
        ],
    )
    if risk_missing:
        errors.append("risk_missing=" + ",".join(risk_missing))

    fxcm_missing = _missing_phrases(
        fxcm_text,
        [
            "Commercial, team, hosted, or redistributed use requires separate written permission from FXCM.",
            "НЕ вендорити",
            "api@fxcm.com",
        ],
    )
    if fxcm_missing:
        errors.append("fxcm_missing=" + ",".join(fxcm_missing))

    ci_missing = _missing_phrases(
        ci_text,
        [
            "tools/exit_gates/manifest.ci.json",
            "pip-audit",
            "bandit",
            "dependency-review-action",
            "npm audit --audit-level=high --omit=dev",
        ],
    )
    if ci_missing:
        errors.append("ci_missing=" + ",".join(ci_missing))

    if errors:
        return {"ok": False, "details": "; ".join(errors)}

    return {
        "ok": True,
        "details": "roles=%d docs=ok ci=ok" % len(copilot_roles),
    }
