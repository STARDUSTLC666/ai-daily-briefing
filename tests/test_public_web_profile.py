import json
from pathlib import Path
import tempfile
import unittest

from briefing.config import load_config, load_sources, project_root
from briefing.pipeline import _source_coverage


class PublicWebProfileTests(unittest.TestCase):
    def test_profile_disables_social_discovery_without_faking_an_audit(self):
        path = project_root() / 'sources.public-web.json'
        sources = load_sources(path)
        self.assertTrue(sources)
        self.assertTrue(any(source.is_official for source in sources))
        self.assertFalse(any(source.id.startswith('x_') or source.type == 'agent_social' for source in sources))
        cfg = load_config(path)
        self.assertTrue(cfg['defaults']['selection_strict_auto'])
        self.assertEqual(cfg['defaults']['lookback_hours'], 24)
        coverage = _source_coverage(sources, [], required=False)['x']
        self.assertEqual(coverage['status'], 'not_requested')
        self.assertFalse(coverage['required'])
        self.assertEqual(coverage['healthy_sources'], 0)

    def test_default_profile_keeps_required_social_audits(self):
        self.assertTrue(_source_coverage([], [])['x']['required'])
        self.assertTrue(any(source.type == 'agent_social' for source in load_sources()))

    def test_inheritance_cycle_fails_with_useful_message(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cycle.json'
            path.write_text(json.dumps({'extends': 'cycle.json'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'circular'):
                load_config(path)
