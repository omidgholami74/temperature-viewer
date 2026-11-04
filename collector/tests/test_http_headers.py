"""The collector must not look like a stock Python urllib client to Cloudflare."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import collector  # noqa: E402


class RequestHeaderTests(unittest.TestCase):
    def test_post_batch_sends_a_descriptive_user_agent(self):
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response

        with mock.patch.object(
            collector.urllib.request,
            "urlopen",
            return_value=response,
        ) as urlopen:
            self.assertTrue(
                collector.post_batch(
                    [{"u": 1, "t": [1.0, 2.0, 3.0, None], "f": [0, 0, 0, 0]}]
                )
            )

        request = urlopen.call_args.args[0]

        self.assertEqual(request.get_header("User-agent"), collector.USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertNotIn("python", collector.USER_AGENT.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
