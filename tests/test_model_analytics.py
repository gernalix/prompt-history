from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prompt_history.cli import _model_rows
from prompt_history.store import connect, ingest_record, init_db


class ModelAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "history.sqlite")
        init_db(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def add_prompt(self, prompt_id: str, *, repo: str = "PersonalHub", task_type: str = "Goal") -> None:
        ingest_record(
            self.db,
            {
                "kind": "prompt",
                "source": "codex-roadmap",
                "source_key": prompt_id,
                "prompt_id": prompt_id,
                "prompt_text": f"PROMPT_ID={prompt_id}",
                "repo": repo,
                "task_type": task_type,
                "text_quality": 100,
            },
        )

    def add_execution(
        self,
        prompt_id: str,
        source_key: str,
        *,
        source: str = "codex-usage",
        model: str = "gpt-6-luna",
        variant: str = "Luna",
        reasoning: str | None = "medium",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        tool_calls: int = 0,
        duration_seconds: float = 1.0,
        result: str = "UNKNOWN",
        ended_at: str = "2026-09-24T10:00:00Z",
    ) -> None:
        ingest_record(
            self.db,
            {
                "kind": "execution",
                "source": source,
                "source_key": source_key,
                "prompt_id": prompt_id,
                "model": model,
                "variant": variant,
                "reasoning": reasoning,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "tool_calls": tool_calls,
                "duration_seconds": duration_seconds,
                "result": result,
                "started_at": ended_at,
                "ended_at": ended_at,
            },
        )

    def test_model_stats_count_each_prompt_once_and_ignore_roadmap_mirror(self) -> None:
        self.add_prompt("111111")
        self.add_execution(
            "111111",
            "cycle-1",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            tool_calls=1,
            duration_seconds=10,
            result="UNKNOWN",
            ended_at="2026-09-24T10:01:00Z",
        )
        self.add_execution(
            "111111",
            "cycle-2",
            input_tokens=150,
            output_tokens=30,
            total_tokens=180,
            tool_calls=2,
            duration_seconds=20,
            result="BLOCKED",
            ended_at="2026-09-24T10:02:00Z",
        )
        # Goal completion rows can report a cumulative total. With detailed
        # cycle deltas present, this must not add another 1000 tokens.
        self.add_execution(
            "111111",
            "cycle-3",
            input_tokens=None,
            output_tokens=None,
            total_tokens=1000,
            tool_calls=0,
            duration_seconds=1,
            result="PASS",
            ended_at="2026-09-24T10:03:00Z",
        )
        # roadmap.sqlite mirrors the same execution history. It is lifecycle
        # evidence, not a second source of token-cost samples.
        self.add_execution(
            "111111",
            "roadmap-cycle",
            source="codex-roadmap",
            input_tokens=9999,
            output_tokens=9999,
            total_tokens=19998,
            tool_calls=99,
            duration_seconds=999,
            result="FAIL",
            ended_at="2026-09-24T10:04:00Z",
        )

        self.add_prompt("222222")
        self.add_execution(
            "222222",
            "cycle-4",
            input_tokens=160,
            output_tokens=40,
            total_tokens=200,
            tool_calls=4,
            duration_seconds=40,
            result="PASS",
            ended_at="2026-09-24T10:04:00Z",
        )

        rows = _model_rows(self.db, "PersonalHub", "Goal")
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(2, row["prompts"])
        self.assertEqual(2, row["executions"])  # compatibility alias: samples, not cycles
        self.assertEqual(2, row["pass_count"])
        self.assertEqual(0, row["blocked_count"])
        self.assertEqual(0, row["fail_count"])
        self.assertEqual(1.0, row["pass_rate"])
        # Prompt 111111 costs 300 detailed tokens, prompt 222222 costs 200.
        self.assertEqual(250.0, row["avg_total_tokens"])
        self.assertEqual(3.5, row["avg_tool_calls"])

        view = self.db.execute(
            """SELECT prompts,executions,pass_count,blocked_count,fail_count,
                      avg_total_tokens,avg_tool_calls
               FROM v_model_performance
               WHERE model='gpt-6-luna' AND reasoning='medium'"""
        ).fetchone()
        self.assertIsNotNone(view)
        self.assertEqual(2, view["prompts"])
        self.assertEqual(2, view["executions"])
        self.assertEqual(2, view["pass_count"])
        self.assertEqual(0, view["blocked_count"])
        self.assertEqual(0, view["fail_count"])
        self.assertEqual(250.0, view["avg_total_tokens"])
        self.assertEqual(3.5, view["avg_tool_calls"])

    def test_fallback_uses_max_total_when_detailed_cycle_tokens_are_absent(self) -> None:
        self.add_prompt("333333")
        self.add_execution(
            "333333",
            "cycle-a",
            input_tokens=None,
            output_tokens=None,
            total_tokens=100,
            result="UNKNOWN",
            ended_at="2026-09-24T10:01:00Z",
        )
        self.add_execution(
            "333333",
            "cycle-b",
            input_tokens=None,
            output_tokens=None,
            total_tokens=150,
            result="PASS",
            ended_at="2026-09-24T10:02:00Z",
        )

        rows = _model_rows(self.db, "PersonalHub", "Goal")
        self.assertEqual(1, len(rows))
        self.assertEqual(1, rows[0]["prompts"])
        self.assertEqual(150.0, rows[0]["avg_total_tokens"])
        self.assertEqual(1, rows[0]["pass_count"])

    def test_mixed_model_prompt_is_excluded_from_model_ranking(self) -> None:
        self.add_prompt("444444")
        self.add_execution(
            "444444",
            "cycle-luna",
            model="gpt-6-luna",
            variant="Luna",
            input_tokens=100,
            output_tokens=10,
            total_tokens=110,
            result="UNKNOWN",
            ended_at="2026-09-24T10:01:00Z",
        )
        self.add_execution(
            "444444",
            "cycle-sol",
            model="gpt-6-sol",
            variant="Sol",
            input_tokens=120,
            output_tokens=20,
            total_tokens=140,
            result="PASS",
            ended_at="2026-09-24T10:02:00Z",
        )

        self.assertEqual([], _model_rows(self.db, "PersonalHub", "Goal"))


if __name__ == "__main__":
    unittest.main()
