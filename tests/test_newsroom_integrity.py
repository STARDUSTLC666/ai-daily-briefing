from pathlib import Path
import unittest
from unittest.mock import patch

from briefing.output_verify import _automatic_news_content_errors
from briefing.render import _browser_slides


class NewsroomIntegrityTests(unittest.TestCase):
    def test_render_input_preserves_long_facts_and_more_than_six_cards(self):
        fact = '研究任务仍需人类设定目标与判断结果。' * 12
        cards = [{'title': f'完整事实{i}', 'body': fact} for i in range(8)]
        segment = {'kind': 'news', 'title': '已核对的完整新闻标题', 'cards': cards,
                   'visual_pages': [{'kind': 'cards', 'cards': cards}]}
        with patch.dict('os.environ', {'BRIEFING_VISUAL_STYLE': 'newsroom'}):
            slides = _browser_slides(Path('preview'), [segment], [60.0], '1080p')
        self.assertEqual(len(slides[0]['page']['cards']), 8)
        self.assertEqual([row['body'] for row in slides[0]['page']['cards']], [fact] * 8)

    def test_scraped_sensational_claim_is_blocked_before_publication(self):
        segment = {'kind': 'news', 'title': 'GPT-6 刷爆榜单，AGI 真的来了',
                   'text': '厂商表示，系统支持在人类指导下完成研究任务，其中部分任务需要多次人工介入。' * 3}
        self.assertTrue(any('sensational copy' in error for error in _automatic_news_content_errors([segment])))
