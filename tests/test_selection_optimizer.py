import unittest

from briefing.selection_optimizer import CandidateProfile, optimize_portfolio


def profile(
    key: str,
    score: float,
    *,
    source: str,
    entity: str,
    category: str,
    topic: str,
    audience: tuple[str, ...] = (),
    paper: bool = False,
    maintenance: bool = False,
    evidence: int = 1,
    claims: int = 1,
    history: int = 0,
) -> CandidateProfile[str]:
    return CandidateProfile(
        item=key,
        key=key,
        source=source,
        entity=entity,
        category=category,
        topic=topic,
        kind=category,
        base_score=score,
        is_paper=paper,
        is_maintenance=maintenance,
        evidence_count=evidence,
        public_claims=claims,
        audience=audience,
        history_count=history,
    )


class SelectionOptimizerTests(unittest.TestCase):
    def test_selects_best_whole_portfolio_instead_of_greedy_prefix(self):
        candidates = [
            profile("a", 100, source="s1", entity="OpenAI", category="release", topic="gpt", audience=("developer",)),
            profile("b", 99, source="s2", entity="OpenAI", category="release", topic="gpt", audience=("developer",)),
            profile("c", 91, source="s3", entity="Anthropic", category="case", topic="claude", audience=("enterprise",), evidence=3, claims=3),
        ]

        result = optimize_portfolio(candidates, max_items=2)

        self.assertEqual(set(result.items), {"a", "c"})
        self.assertEqual(result.diagnostics["mode"], "portfolio_beam_search")
        self.assertEqual(result.diagnostics["selected_count"], 2)

    def test_category_limit_is_hard_constraint(self):
        candidates = [
            profile("model-a", 99, source="s1", entity="A", category="model", topic="a"),
            profile("model-b", 98, source="s2", entity="B", category="model", topic="b"),
            profile("product", 70, source="s3", entity="C", category="product", topic="c"),
        ]

        result = optimize_portfolio(candidates, max_items=2, category_limits={"model": 1})

        self.assertEqual(len(result.items), 2)
        self.assertEqual(sum(item.startswith("model") for item in result.items), 1)
        self.assertIn("product", result.items)

    def test_source_and_paper_limits_can_leave_digest_short(self):
        candidates = [
            profile("p1", 99, source="arxiv", entity="Paper", category="research", topic="p1", paper=True),
            profile("p2", 98, source="arxiv", entity="Paper", category="research", topic="p2", paper=True),
            profile("p3", 97, source="arxiv", entity="Other", category="research", topic="p3", paper=True),
        ]

        result = optimize_portfolio(candidates, max_items=3)

        self.assertEqual(len(result.items), 1)

    def test_recent_repeat_loses_to_equally_useful_fresh_story(self):
        candidates = [
            profile("repeat", 100, source="s1", entity="A", category="product", topic="repeat", history=2),
            profile("fresh", 88, source="s2", entity="B", category="product", topic="fresh"),
        ]

        result = optimize_portfolio(candidates, max_items=1)

        self.assertEqual(result.items, ["fresh"])
        self.assertEqual(result.diagnostics["history_penalty_items"], {})

    def test_repeat_remains_eligible_when_no_fresh_alternative_exists(self):
        candidate = profile("repeat", 100, source="s1", entity="A", category="product", topic="repeat", history=2)

        result = optimize_portfolio([candidate], max_items=1)

        self.assertEqual(result.items, ["repeat"])
        self.assertEqual(result.diagnostics["history_penalty_items"], {"repeat": 2})

    def test_strict_mode_limits_maintenance_stories(self):
        candidates = [
            profile("release-a", 99, source="github-a", entity="A", category="maintenance", topic="a", maintenance=True),
            profile("release-b", 98, source="github-b", entity="B", category="maintenance", topic="b", maintenance=True),
        ]

        result = optimize_portfolio(candidates, max_items=2, strict_auto=True)

        self.assertEqual(len(result.items), 1)
        self.assertGreater(result.diagnostics["constraint_rejections_explored"].get("maintenance_limit", 0), 0)


if __name__ == "__main__":
    unittest.main()
