import sqlite3
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock

from vote_panel import VotePanel, make_embed, JST


def payload():
    return {"ok": True, "available": True, "rankingVisibility": "live",
            "updatedAt": "2026-11-11T08:38:02Z",
            "candidates": [{"id": "other", "rank": 1, "votes": 999},
                           {"id": "egk-esports", "rank": 5, "votes": 56}]}


class EmbedTests(unittest.TestCase):
    def test_only_egk_and_zero_votes(self):
        data = payload()
        data["candidates"][1]["votes"] = 0
        e = make_embed(data, now=datetime(2026, 11, 11, 18, tzinfo=JST))
        self.assertEqual(e.fields[0].value, "**5位**")
        self.assertEqual(e.fields[1].value, "**0票**")

    def test_blackout_removes_scores_even_on_fetch_failure(self):
        for data, failed in [(payload(), False), (None, True)]:
            e = make_embed(data, failed=failed, now=datetime(2026, 11, 14, 22, tzinfo=JST))
            self.assertIn("非公開", e.description)
            self.assertEqual(len(e.fields), 0)

    def test_api_hidden_outside_scheduled_blackout(self):
        data = payload()
        data["rankingVisibility"] = "hidden"
        e = make_embed(data, now=datetime(2026, 11, 11, 18, tzinfo=JST))
        self.assertEqual(len(e.fields), 0)

    def test_pre_blackout_snapshot_not_shown_as_final(self):
        e = make_embed(payload(), now=datetime(2026, 11, 15, 12, tzinfo=JST))
        self.assertIn("発表待ち", e.description)
        self.assertEqual(len(e.fields), 0)

    def test_new_snapshot_shown_as_result(self):
        data = payload()
        data["updatedAt"] = "2026-11-15T03:00:00Z"
        e = make_embed(data, now=datetime(2026, 11, 15, 12, tzinfo=JST))
        self.assertEqual(e.description, "公式発表結果")

    def test_invalid_or_missing_egk_is_not_zero(self):
        for candidates in [[], [{"id": "egk-esports", "rank": True, "votes": 3}]]:
            data = payload()
            data["candidates"] = candidates
            with self.assertRaises(ValueError):
                make_embed(data, now=datetime(2026, 11, 11, 18, tzinfo=JST))


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_uses_saved_message_without_posting(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        bot = Mock()
        VotePanel(bot, db)
        db.execute("INSERT INTO vote_panels VALUES(1,2,3)")
        db.commit()
        restarted = VotePanel(bot, db)
        channel = Mock()
        message = Mock(embeds=[])
        message.edit = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot.get_channel.return_value = channel
        restarted.fetch_embed = AsyncMock(return_value=make_embed(failed=True))
        await restarted.refresh()
        channel.fetch_message.assert_awaited_once_with(3)
        message.edit.assert_awaited_once()
        channel.send.assert_not_called()
        db.close()


if __name__ == "__main__":
    unittest.main()
