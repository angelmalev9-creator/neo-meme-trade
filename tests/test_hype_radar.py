import json
import unittest
from unittest.mock import patch

import hype_radar as hr

NOW = 1_800_000_000_000
RSS = """<?xml version="1.0"?><rss><channel><item><title>Elon Musk unveils &quot;Doge&quot; robot</title></item>
<item><title>Rate decision shakes markets</title></item><item><title></title></item></channel></rss>"""
REDDIT = {'data': {'children': [{'data': {'title': 'Skibidi toilet is back', 'ups': 9000}}, {'data': {'title': 'quiet post'}}, {'data': {}}]}}
GECKO = {'coins': [{'item': {'name': 'Pudgy Penguins', 'symbol': 'PENGU'}}, {'item': {}}]}
BOOSTS = [{'chainId': 'solana', 'tokenAddress': 'A' * 44, 'description': 'the robot dog coin'}, {'chainId': 'ethereum', 'tokenAddress': 'B'}]
ANSWER = json.dumps([
    {'theme': 'Musk robot dog', 'keywords': ['Doge', 'robot', 'optimus', 'the', 'ro'], 'hype': 91, 'category': 'news', 'why': 'top story', 'sources': [0, 3, 99]},
    {'theme': 'Skibidi', 'keywords': ['skibidi', 'toilet'], 'hype': 70.4, 'category': 'meme', 'why': 'viral', 'sources': [2]},
    {'theme': 'no keywords', 'keywords': ['coin'], 'hype': 50},
    'junk',
])


def fetch(url, kind):
    return {'rss': RSS, 'reddit': REDDIT, 'coingecko': GECKO, 'dexscreener': BOOSTS}[kind]


class Sources(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(hr.parse_rss_titles(RSS), ['Elon Musk unveils "Doge" robot', 'Rate decision shakes markets'])
        self.assertEqual(hr.parse_reddit_titles(REDDIT), ['Skibidi toilet is back (↑9000)', 'quiet post'])
        self.assertEqual(hr.parse_coingecko_trending(GECKO), ['trending: Pudgy Penguins (PENGU)'])
        self.assertEqual(len(hr.parse_dexscreener_boosts(BOOSTS)), 1)
        self.assertEqual(hr.parse_rss_titles('not xml'), [])

    def test_collect_numbers_lines_and_reports_errors(self):
        def flaky(url, kind):
            if kind == 'coingecko':
                raise RuntimeError('429 too many')
            return fetch(url, kind)
        with patch.object(hr, 'x_signal_lines', return_value=['@elonmusk: look at this']):
            lines, status = hr.collect_sources(flaky)
        self.assertEqual(status['coingecko-trending'][:9], 'error:429')
        self.assertEqual(status['google-news-top'], 'ok:2')
        self.assertEqual(lines[-1], {'source': 'x-signal', 'text': '@elonmusk: look at this'})
        self.assertTrue(all(set(row) == {'source', 'text'} for row in lines))


class Themes(unittest.TestCase):
    def test_parse_normalises_keywords_and_drops_bad_rows(self):
        themes = hr.parse_themes_text('```json\n' + ANSWER + '\n```', generated_at=NOW, line_count=10)
        self.assertEqual([t['theme'] for t in themes], ['Musk robot dog', 'Skibidi'])
        self.assertEqual(themes[0]['keywords'], ['doge', 'robot', 'optimus'])
        self.assertEqual((themes[0]['hype'], themes[0]['sources']), (91.0, [0, 3]))
        self.assertEqual(themes[1]['hype'], 70.4)
        self.assertEqual(hr.parse_themes_text('no json here', generated_at=NOW, line_count=1), [])

    def test_match_token_prefers_ticker_and_respects_freshness_and_hype(self):
        themes = hr.parse_themes_text(ANSWER, generated_at=NOW, line_count=10)
        self.assertEqual(hr.match_token({'name': 'Robot Dog Coin', 'symbol': 'RDOG'}, themes, now=NOW)['keyword'], 'robot')
        doge = hr.match_token({'name': 'Something', 'symbol': 'DOGE'}, themes, now=NOW)
        self.assertEqual((doge['theme'], doge['keyword'], doge['hype']), ('Musk robot dog', 'doge', 91.0))
        self.assertGreater(doge['score'], hr.match_token({'name': 'Robot Dog Coin', 'symbol': 'RDOG'}, themes, now=NOW)['score'])
        # 'doge' is only four letters: inside a name it counts, as a substring of an unrelated ticker it does not.
        self.assertIsNotNone(hr.match_token({'name': 'DogeKing', 'symbol': 'DK'}, themes, now=NOW))
        self.assertIsNone(hr.match_token({'name': 'Banana', 'symbol': 'BNN'}, themes, now=NOW))
        self.assertIsNone(hr.match_token({'name': 'Skibidi Rizz', 'symbol': 'SKB'}, themes, now=NOW, min_hype=80))
        self.assertIsNone(hr.match_token({'name': 'Skibidi Rizz', 'symbol': 'SKB'}, themes, now=NOW + hr.THEME_TTL_SECONDS * 1000 + 1))
        self.assertIsNone(hr.match_token({}, themes, now=NOW))
        self.assertEqual(len(hr.active_themes({'themes': themes}, now=NOW)), 2)
        self.assertEqual(hr.active_themes({'themes': themes}, now=NOW + hr.THEME_TTL_SECONDS * 1000 + 1), [])


class Poll(unittest.TestCase):
    def llm(self, messages):
        self.messages = messages
        return {'choices': [{'message': {'content': ANSWER}}], 'usage': {'prompt_tokens': 4000, 'completion_tokens': 500}}

    def test_poll_writes_themes_budget_and_sources(self):
        with patch.object(hr, 'LLM_KEY', 'k'), patch.object(hr, 'x_signal_lines', return_value=[]), patch.object(hr, 'utc_day', return_value='2026-10-08'):
            state = hr.poll_once({}, fetch=fetch, llm=self.llm, now=NOW)
        self.assertEqual(state['status'], 'online')
        self.assertEqual([t['theme'] for t in state['themes']], ['Musk robot dog', 'Skibidi'])
        self.assertEqual(state['budget']['calls'], 1)
        self.assertAlmostEqual(state['budget']['spent_usd'], 4000 / 1e6 * hr.INPUT_MTOKEN_USD + 500 / 1e6 * hr.OUTPUT_MTOKEN_USD)
        self.assertIn('[0] (google-news-top) Elon Musk', self.messages[1]['content'])
        self.assertEqual(state['source_status']['reddit-memes'], 'ok:2')

    def test_missing_key_and_budget_pause_do_not_call_the_model(self):
        with patch.object(hr, 'LLM_KEY', ''):
            self.assertEqual(hr.poll_once({}, fetch=fetch, llm=self.llm, now=NOW)['status'], 'missing_api_key')
        with patch.object(hr, 'LLM_KEY', 'k'), patch.object(hr, 'DAILY_BUDGET_USD', 1.0), patch.object(hr, 'utc_day', return_value='d'):
            state = hr.poll_once({'budget': {'day': 'd', 'spent_usd': 1.0}, 'themes': [{'theme': 'old', 'keywords': ['old'], 'hype': 90, 'generated_at': NOW - 1000}]}, fetch=fetch, llm=self.llm, now=NOW)
        self.assertEqual(state['status'], 'budget_paused')
        self.assertEqual(state['themes'][0]['theme'], 'old')

    def test_empty_answer_uses_current_source_fallback(self):
        def empty(messages):
            return {'choices': [{'message': {'content': 'sorry'}}], 'usage': {}}
        previous = {'themes': [{'theme': 'old', 'keywords': ['old'], 'hype': 90, 'generated_at': NOW - 1000}]}
        with patch.object(hr, 'LLM_KEY', 'k'), patch.object(hr, 'x_signal_lines', return_value=[]):
            state = hr.poll_once(previous, fetch=fetch, llm=empty, now=NOW)
        self.assertEqual((state['status'], state['theme_generation']), ('online', 'source_fallback'))
        self.assertTrue(state['themes'])
        self.assertTrue(all(theme['generated_at'] == NOW for theme in state['themes']))

    def test_provider_403_does_not_take_hype_offline(self):
        def forbidden(messages):
            raise RuntimeError('403 Forbidden')
        with patch.object(hr, 'LLM_KEY', 'k'), patch.object(hr, 'x_signal_lines', return_value=[]):
            state = hr.poll_once({}, fetch=fetch, llm=forbidden, now=NOW)
        self.assertEqual(state['status'], 'online')
        self.assertEqual(state['theme_generation'], 'source_fallback')
        self.assertIn('403 Forbidden', state['llm_error'])
        self.assertNotIn('error', state)
        self.assertGreater(len(state['themes']), 0)


if __name__ == '__main__':
    unittest.main()
