import json
import unittest

from briefing.collect import parse_hf_models
from briefing.models import Source


class HuggingFaceModelsParseTests(unittest.TestCase):
    def test_parse_hf_models_api(self):
        src = Source(
            id="qwen_hf_models",
            name="Qwen Hugging Face Models",
            tier="A",
            type="hf_models",
            region="cn",
            url="https://huggingface.co/api/models?author=Qwen",
            reliability="official",
        )
        text = json.dumps(
            [
                {
                    "modelId": "Qwen/Qwen3-ASR-0.6B-hf",
                    "lastModified": "2026-06-26T08:39:37.000Z",
                    "pipeline_tag": "automatic-speech-recognition",
                    "tags": ["qwen3", "audio"],
                    "downloads": 1234,
                    "likes": 56,
                }
            ]
        )
        items = parse_hf_models(text, src, src.url)
        self.assertEqual(len(items), 1)
        self.assertIn("Qwen/Qwen3-ASR", items[0].title)
        self.assertEqual(items[0].link, "https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf")
        self.assertIsNotNone(items[0].published_at)
        self.assertIn("qwen3", items[0].summary)


if __name__ == "__main__":
    unittest.main()
