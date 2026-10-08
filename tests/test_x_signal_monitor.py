import json
import os
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('NEO_X_SIGNAL_HANDLES','elonmusk')
import x_signal_monitor as x

ADDRESS='So11111111111111111111111111111111111111112'


class XSignalMonitorTests(unittest.TestCase):
    def test_parse_json_only_response(self):
        payload=[{'handle':'elonmusk','post_id':'1','text':f'CA {ADDRESS}','addresses':[ADDRESS]}]
        self.assertEqual(x.parse_json_text(json.dumps(payload))[0]['post_id'],'1')
        self.assertEqual(x.parse_json_text('```json\n'+json.dumps(payload)+'\n```')[0]['post_id'],'1')
        self.assertEqual(x.parse_json_text('not json'),[])

    def test_accepts_only_literal_address_from_allowed_handle(self):
        now=1_800_000_000_000
        row={'handle':'elonmusk','post_id':'42','post_url':'https://x.com/elonmusk/status/42',
             'text':f'New token CA: {ADDRESS}','addresses':[ADDRESS]}
        signal=x.normalize_signal(row,now)
        self.assertIsNotNone(signal)
        self.assertEqual(signal['addresses'],[ADDRESS])

        invented={**row,'text':'No contract address in this post.'}
        self.assertIsNone(x.normalize_signal(invented,now))
        wrong_handle={**row,'handle':'not-allowed'}
        self.assertIsNone(x.normalize_signal(wrong_handle,now))

    def test_usage_cost_counts_x_and_model_usage(self):
        cost,posts,users,input_tokens,output_tokens=x.usage_cost({
            'input_tokens':1_000_000,'output_tokens':1_000_000,
            'server_side_tool_usage_details':{'x_posts_fetched':2,'x_users_fetched':1},
        })
        self.assertEqual((posts,users,input_tokens,output_tokens),(2,1,1_000_000,1_000_000))
        self.assertAlmostEqual(cost,1.25+2.50+0.010+0.010)


if __name__=='__main__':
    unittest.main()
