<!-- CODEX_REQUIREMENTS_JSON_START -->
{
  "schema_version": 1,
  "brief_id": "REQ-001",
  "revision": 1,
  "target_release": "MVP-1",
  "status": "draft",
  "requirements": {
    "scope": {"in": ["<confirmed in-scope outcome>"], "out": ["<confirmed non-goal>"]},
    "users": [{"id": "REQ-U-001", "text": "<user>", "status": "unconfirmed", "source": "<user source>"}],
    "outcomes": [{"id": "REQ-O-001", "text": "<observable outcome>", "status": "unconfirmed", "source": "<user source>"}],
    "flows": [{"id": "REQ-FLOW-001", "steps": ["<step>"], "status": "unconfirmed", "source": "<user source>"}],
    "capabilities": [{"id": "REQ-F-001", "priority": "must", "preconditions": [], "inputs": [], "action": "<action>", "observable_result": "<result>", "boundaries": [], "status": "unconfirmed", "source": "<user source>"}],
    "scenarios": [
      {"id": "REQ-S-001", "type": "happy", "applicability": "required", "na_reason": null, "na_source": null, "expected": "<expected>", "status": "unconfirmed", "source": "<user source>"},
      {"id": "REQ-S-002", "type": "failure", "applicability": "required", "na_reason": null, "na_source": null, "expected": "<expected failure behavior>", "status": "unconfirmed", "source": "<user source>"},
      {"id": "REQ-S-003", "type": "non_goal", "applicability": "required", "na_reason": null, "na_source": null, "expected": "<explicitly not supported>", "status": "unconfirmed", "source": "<user source>"}
    ],
    "non_goals": [{"id": "REQ-NG-001", "text": "<non-goal>", "status": "unconfirmed", "source": "<user source>"}],
    "constraints": [],
    "open_questions": [{"id": "REQ-Q-001", "blocking": true, "status": "open", "owner": "<owner>", "resolution_target": "before approval", "source": "<source>", "resolution": null, "resolved_by": null, "resolved_source": null}],
    "calibration_rounds": [{"id": "CAL-001", "result": "corrected", "changed_requirement_ids": [], "source": "<user feedback source>"}]
  },
  "approval": {"approved_by": null, "approved_at": null, "source": null, "approved_fingerprint": null}
}
<!-- CODEX_REQUIREMENTS_JSON_END -->

# Requirements Brief REQ-001

## 原始目标与来源

<保留用户原始目标；AI 建议单独标为假设。>

## 已确认 / 假设 / 未确认

<按稳定 ID 复述使用者、结果、流程、范围、非目标、约束和风险。>

## 场景与验收样例

<引用 JSON 中同一 REQ ID，覆盖正常、边界或失败、明确反例。>

## 反馈记录

<记录每轮 confirmed/corrected，以及受影响的需求 ID。>

