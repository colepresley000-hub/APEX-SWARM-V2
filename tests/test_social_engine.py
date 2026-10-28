"""
End-to-end tests for reply_engine + feedback + core helpers.
All X / Telegram / Anthropic calls are mocked: nothing posts, nothing is sent.
Run:  python3 -m unittest discover -s tests -v
"""
import json, os, sys, tempfile, unittest, pathlib
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_APPROVAL_BOT_TOKEN", "test-token")

import requests, tweepy
import social_core as sc
import reply_engine as re_
import feedback as fb


def make_brand(tmp, active=True):
    os.environ["T_TOK"] = "tok"; os.environ["T_SEC"] = "sec"; os.environ["T_DRAFT"] = "false"
    return sc.BrandConfig(
        key="t", name="T", handle="t", url="t.example", user_id="1",
        system_prompt="sys", product_context="ctx",
        categories=[{"name": "a", "weight": 1, "style": "s", "brief": "b"}],
        access_token_env="T_TOK", access_secret_env="T_SEC", draft_env="T_DRAFT",
        reply_keywords=['raising "capital"', "  ", "african saas"],
        reply_daily_cap=3, active=active)


def http_error(cls, code, body):
    r = requests.Response(); r.status_code = code
    r._content = json.dumps(body).encode()
    r.headers["Content-Type"] = "application/json"
    return cls(r)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(sc, "DATA_DIR", pathlib.Path(self.tmp.name)); p.start()
        self.addCleanup(p.stop); self.addCleanup(self.tmp.cleanup)
        self.brand = make_brand(self.tmp)
        mock.patch.object(re_, "_DEDICATED", True).start()
        mock.patch.object(re_.time, "sleep", lambda s: None).start()
        self.addCleanup(mock.patch.stopall)
        re_.BRANDS["t"] = self.brand
        self.addCleanup(lambda: re_.BRANDS.pop("t", None))


class QueueTests(Base):
    def test_load_tolerates_missing_corrupt_and_wrong_type(self):
        f = self.brand.reply_queue_file
        for content in ('{"pending": []}', "not json", "[1,2]", ""):
            f.write_text(content)
            q = re_.load_queue(self.brand)
            self.assertEqual((q["pending"], q["seen_ids"], q["history"]), ([], [], []))

    def test_save_is_atomic_no_tmp_left(self):
        re_.save_queue(self.brand, {"pending": [], "seen_ids": ["1"] * 3000, "history": []})
        self.assertEqual(len(re_.load_queue(self.brand)["seen_ids"]), 2000)
        self.assertEqual([p.name for p in pathlib.Path(self.tmp.name).glob("*.tmp")], [])

    def test_daily_cap_counts_only_approved_today(self):
        from datetime import datetime
        now = datetime.now().isoformat()
        q = {"history": [{"status": "approved", "at": now}, {"status": "failed", "at": now},
                         {"status": "approved", "at": "2000-01-01T00:00:00"}]}
        self.assertEqual(re_._approved_today(q), 1)


class CandidateTests(Base):
    def _client(self, tweets):
        c = mock.Mock()
        c.search_recent_tweets.return_value = NS(data=tweets)
        return c

    def _tw(self, i, rs="everyone"):
        return NS(id=i, text=f"t{i}", author_id=9, public_metrics={"like_count": 0}, reply_settings=rs)

    def test_filters_restricted_seen_and_duplicates_and_strips_quotes(self):
        c = self._client([self._tw(1), self._tw(2, "following"), self._tw(3), self._tw(3), self._tw(4)])
        with mock.patch.object(sc, "v2_client", return_value=c):
            out = re_.find_candidates(self.brand, {"seen_ids": ["4"]})
        self.assertEqual([x["id"] for x in out], ["1", "3"])
        query = c.search_recent_tweets.call_args.kwargs["query"]
        self.assertIn('"raising capital"', query)      # embedded quotes stripped
        self.assertNotIn('""', query)                  # blank keyword dropped

    def test_search_403_and_network_errors_do_not_crash(self):
        c = mock.Mock()
        c.search_recent_tweets.side_effect = http_error(tweepy.errors.Forbidden, 403, {"detail": "x"})
        with mock.patch.object(sc, "v2_client", return_value=c):
            self.assertEqual(re_.find_candidates(self.brand, {"seen_ids": []}), [])
        c.search_recent_tweets.side_effect = ConnectionResetError("boom")
        with mock.patch.object(sc, "v2_client", return_value=c):
            self.assertEqual(re_.find_candidates(self.brand, {"seen_ids": []}), [])

    def test_network_mode_rotates_offset_and_wraps(self):
        follows = [{"id": str(i), "username": f"u{i}"} for i in range(20)]
        c = self._client([])
        with mock.patch.object(re_, "_load_follows", return_value=follows), \
             mock.patch.object(sc, "v2_client", return_value=c):
            q = {"seen_ids": [], "net_offset": 15}
            re_.find_candidates_network(self.brand, q)
        qs = c.search_recent_tweets.call_args.kwargs["query"]
        self.assertIn("from:u15", qs); self.assertIn("from:u0", qs)   # wrapped
        self.assertEqual(q["net_offset"], (15 + re_.NETWORK_CHUNK) % 20)


class DraftReplyTests(Base):
    def _llm(self, text):
        msg = NS(content=[NS(type="text", text=text)], usage=NS())
        return mock.patch.object(sc, "anthropic_client",
                                 return_value=mock.Mock(messages=mock.Mock(create=mock.Mock(return_value=msg))))

    def test_exact_skip_dropped_but_real_reply_starting_with_skip_kept(self):
        for t in ("SKIP", "skip.", "Skip!"):
            with self._llm(t):
                self.assertEqual(re_.draft_reply(self.brand, "x"), "")
        with self._llm("Skip the VA and automate the proposals first."):
            self.assertTrue(re_.draft_reply(self.brand, "x").startswith("Skip the VA"))

    def test_empty_response_is_safe(self):
        msg = NS(content=[], usage=NS())
        with mock.patch.object(sc, "anthropic_client", return_value=mock.Mock(
                messages=mock.Mock(create=mock.Mock(return_value=msg)))):
            self.assertEqual(re_.draft_reply(self.brand, "x"), "")


class PostReplyTests(Base):
    item = {"reply_text": "hi", "tweet_id": "9", "queued_at": ""}

    def test_inactive_stale_success_noid(self):
        b = make_brand(self.tmp, active=False)
        ok, note = re_._post_reply(b, self.item); self.assertFalse(ok); self.assertIn("shut down", note)
        from datetime import datetime, timedelta
        stale = dict(self.item, queued_at=(datetime.now() - timedelta(hours=30)).isoformat())
        ok, note = re_._post_reply(self.brand, stale); self.assertFalse(ok); self.assertIn("too old", note)
        c = mock.Mock(); c.create_tweet.return_value = NS(data={"id": "5"})
        with mock.patch.object(sc, "v2_client", return_value=c):
            self.assertEqual(re_._post_reply(self.brand, self.item)[0], True)
        c.create_tweet.return_value = NS(data=None)
        with mock.patch.object(sc, "v2_client", return_value=c):
            self.assertEqual(re_._post_reply(self.brand, self.item)[0], False)

    def test_forbidden_restricted_vs_other_and_credit_alert(self):
        c = mock.Mock()
        c.create_tweet.side_effect = http_error(
            tweepy.errors.Forbidden, 403, {"detail": "not been mentioned or otherwise engaged"})
        with mock.patch.object(sc, "v2_client", return_value=c):
            ok, note = re_._post_reply(self.brand, self.item)
        self.assertFalse(ok); self.assertIn("restricted", note)
        c.create_tweet.side_effect = http_error(tweepy.errors.HTTPException, 402, {"detail": "Payment Required"})
        with mock.patch.object(sc, "v2_client", return_value=c), \
             mock.patch.object(sc, "notify_credits_empty") as n:
            ok, note = re_._post_reply(self.brand, self.item)
        self.assertFalse(ok); self.assertIn("credits", note); n.assert_called_once()


class Stop(BaseException):
    pass


class WorkerTests(Base):
    def _run_worker(self, updates, pending, post_result=(True, "posted ✅")):
        re_.save_queue(self.brand, {"pending": pending, "seen_ids": [], "history": []})
        calls, polls = [], []

        def fake_tg(method, payload):
            calls.append((method, payload))
            if method == "getUpdates":
                polls.append(payload.get("offset", "ABSENT"))
                if len(polls) == 1:
                    return {"ok": True, "result": updates}
                raise Stop()
            return {"ok": True}

        with mock.patch.object(re_, "_tg", fake_tg), \
             mock.patch.object(re_, "_post_reply", return_value=post_result) as pr:
            with self.assertRaises(Stop):
                re_.worker()
        return calls, polls, pr

    def cb(self, uid, data, msg=True):
        return {"update_id": uid, "callback_query": {
            "id": f"cq{uid}", "data": data,
            **({"message": {"chat": {"id": 1}, "message_id": 5, "text": "draft"}} if msg else {})}}

    def test_first_poll_omits_offset_and_second_advances_it(self):
        calls, polls, _ = self._run_worker([self.cb(10, "rok:t:t_1")], [{"id": "t_1"}])
        self.assertEqual(polls, ["ABSENT", 11])

    def test_approve_posts_removes_pending_logs_history_and_edits_message(self):
        calls, _, pr = self._run_worker([self.cb(1, "rok:t:t_1")], [{"id": "t_1"}])
        pr.assert_called_once()
        q = re_.load_queue(self.brand)
        self.assertEqual(q["pending"], [])
        self.assertEqual(q["history"][0]["status"], "approved")
        methods = [m for m, _ in calls]
        self.assertIn("answerCallbackQuery", methods); self.assertIn("editMessageText", methods)

    def test_skip_does_not_post(self):
        _, _, pr = self._run_worker([self.cb(1, "rno:t:t_1")], [{"id": "t_1"}])
        pr.assert_not_called()
        self.assertEqual(re_.load_queue(self.brand)["history"][0]["status"], "skipped")

    def test_failed_post_recorded_as_failed(self):
        self._run_worker([self.cb(1, "rok:t:t_1")], [{"id": "t_1"}], post_result=(False, "post FAILED"))
        self.assertEqual(re_.load_queue(self.brand)["history"][0]["status"], "failed")

    def test_malformed_unknown_and_handled_updates_never_crash_worker(self):
        bad = [{"no_update_id": 1}, self.cb(2, "garbage"), self.cb(3, "rok:"), self.cb(4, "rok:t"),
               self.cb(5, "rok:nope:x_1"), self.cb(6, "rok:t:missing"),
               {"update_id": 7, "callback_query": {"data": "rok:t:t_1"}},      # no id
               self.cb(8, "rok:t:t_1", msg=False)]                           # no message
        calls, polls, pr = self._run_worker(bad, [{"id": "t_1"}])
        self.assertEqual(polls[-1], 9)            # offset advanced past every update
        pr.assert_called_once()                   # only the valid one posted


class ScanTests(Base):
    def test_scan_merge_keeps_worker_updates_and_respects_cap_and_delivery(self):
        re_.save_queue(self.brand, {"pending": [{"id": "t_1"}], "seen_ids": ["1"], "history": []})

        def candidates(brand, q):
            w = re_.load_queue(brand); w["pending"] = []
            w["history"].append({"id": "t_1", "status": "approved", "at": "2026-01-01T00:00:00"})
            re_.save_queue(brand, w)                       # worker taps while scan runs
            q["net_offset"] = 7
            return [{"id": str(i), "text": "x"} for i in range(2, 8)]
        sent = []
        with mock.patch.object(re_, "REPLY_MODE", "network"), \
             mock.patch.object(re_, "find_candidates_network", candidates), \
             mock.patch.object(re_, "draft_reply", side_effect=lambda b, t: "ok reply"), \
             mock.patch.object(re_, "send_for_approval", side_effect=lambda b, it: sent.append(it["id"]) or it["id"] != "t_3"):
            re_.scan(self.brand)
        q = re_.load_queue(self.brand)
        ids = [p["id"] for p in q["pending"]]
        self.assertNotIn("t_1", ids)                       # not resurrected
        self.assertEqual(q["history"][0]["status"], "approved")
        self.assertNotIn("t_3", ids)                       # undelivered draft not left pending
        self.assertTrue(any(h["status"] == "delivery_failed" for h in q["history"]))
        self.assertEqual(q["net_offset"], 7)
        self.assertLessEqual(len(ids), self.brand.reply_daily_cap)

    def test_inactive_brand_scan_is_noop(self):
        b = make_brand(self.tmp, active=False)
        with mock.patch.object(re_, "find_candidates_network") as f:
            re_.scan(b)
        f.assert_not_called()


class FeedbackTests(Base):
    def _posted(self, entries):
        self.brand.posted_file.write_text(json.dumps(entries))

    def test_no_api_call_when_nothing_to_score(self):
        with mock.patch.object(sc, "v2_client") as vc:
            self.assertEqual(fb.refresh_winners(self.brand)["scored"], 0)
            self._posted([{"text": "no id"}])
            self.assertEqual(fb.refresh_winners(self.brand)["scored"], 0)
        vc.assert_not_called()

    def test_scores_matches_int_and_str_ids_and_writes_winners(self):
        self._posted([{"id": 111, "text": "a", "category": "x"}, {"id": "222", "text": "b", "category": "y"},
                      {"id": "333", "text": "c", "category": "y"}])
        m = lambda l, r, rp: {"like_count": l, "retweet_count": r, "reply_count": rp, "quote_count": 0}
        c = mock.Mock(); c.get_tweets.return_value = NS(data=[
            NS(id=111, public_metrics=m(1, 0, 0)), NS(id=222, public_metrics=m(0, 2, 1)),
            NS(id=333, public_metrics=None)])
        with mock.patch.object(sc, "v2_client", return_value=c):
            out = fb.refresh_winners(self.brand)
        self.assertEqual(out["scored"], 3)
        win = json.loads(self.brand.winners_file.read_text())
        self.assertEqual([w["id"] for w in win], ["222", "111"])      # 10 > 1; zero-score excluded
        self.assertEqual(win[0]["score"], 2 * 3 + 1 * 4)

    def test_batches_of_100_and_api_errors_skipped(self):
        self._posted([{"id": str(i), "text": "t", "category": "c"} for i in range(1, 151)])
        c = mock.Mock(); c.get_tweets.side_effect = [tweepy.TweepyException("rate"), NS(data=None)]
        with mock.patch.object(sc, "v2_client", return_value=c), mock.patch.object(fb, "LOOKBACK", 150):
            out = fb.refresh_winners(self.brand)
        self.assertEqual(c.get_tweets.call_count, 2)      # 150 ids -> batches of 100 + 50
        self.assertEqual(len(c.get_tweets.call_args_list[0].args[0]), 100)
        self.assertEqual(out["scored"], 0)                # error batch skipped, empty batch ok

    def test_inactive_brand_skipped(self):
        b = make_brand(self.tmp, active=False)
        self.assertEqual(fb.refresh_winners(b)["skipped"], "inactive")

    def test_total_outage_keeps_existing_winners(self):
        self.brand.winners_file.write_text(json.dumps([{"id": "1", "text": "keep me", "score": 9}]))
        self._posted([{"id": "5", "text": "t", "category": "c"}])
        c = mock.Mock(); c.get_tweets.side_effect = tweepy.TweepyException("down")
        with mock.patch.object(sc, "v2_client", return_value=c):
            out = fb.refresh_winners(self.brand)
        self.assertEqual(out["error"], "metrics unavailable")
        self.assertEqual(json.loads(self.brand.winners_file.read_text())[0]["text"], "keep me")


class CoreTests(Base):
    def test_kill_switch_every_entrypoint(self):
        b = make_brand(self.tmp, active=False)
        with mock.patch.object(sc, "v2_client") as vc:
            self.assertIsNone(sc.post_tweet(b, "x", "c"))
            self.assertIsNone(sc.post_thread(b, ["a", "b"]))
            self.assertIsNone(sc.autopost(b))
        vc.assert_not_called()

    def test_explicit_draft_env_beats_dotenv_and_default_is_live(self):
        with mock.patch.dict(os.environ, {"T_DRAFT": "true"}):
            self.assertTrue(self.brand.draft_mode)
        self.assertFalse(self.brand.draft_mode)

    def test_truncate_keeps_link_and_thread_guard(self):
        u = "t.example"
        out = sc.truncate_keep_url("word " * 80 + u, u)
        self.assertTrue(out.endswith(u) and len(out) <= 280)
        with mock.patch.object(sc, "anthropic_client", return_value=mock.Mock(messages=mock.Mock(
                create=mock.Mock(return_value=NS(content=[NS(type="text", text="only one")], usage=NS(
                    input_tokens=1, output_tokens=1)))))):
            self.assertEqual(sc.generate_thread(self.brand, "topic"), [])

    def test_post_tweet_partial_thread_logged_and_credit_error_alerts(self):
        c = mock.Mock()
        c.create_tweet.side_effect = [NS(data={"id": "1"}),
                                      http_error(tweepy.errors.HTTPException, 402, {"detail": "Payment Required"})]
        with mock.patch.object(sc, "v2_client", return_value=c), mock.patch.object(sc.time, "sleep", lambda s: None), \
             mock.patch.object(sc, "notify_credits_empty") as n:
            self.assertIsNone(sc.post_thread(self.brand, ["a", "b", "c"]))
        n.assert_called_once()
        logged = sc.load_posted(self.brand)
        self.assertTrue(logged[0]["partial"]); self.assertEqual(logged[0]["thread_ids"], ["1"])


class BrandConfigTests(unittest.TestCase):
    def setUp(self):
        import brands
        self.b = brands

    def test_every_brand_is_valid(self):
        keys, envs = set(), set()
        for br in self.b.BRANDS.values():
            self.assertNotIn(br.key, keys); keys.add(br.key)
            for e in (br.access_token_env, br.access_secret_env):
                self.assertNotIn(e, envs); envs.add(e)           # no shared token vars
            names = [c["name"] for c in br.categories]
            self.assertEqual(len(names), len(set(names)), br.key)
            for c in br.categories:
                self.assertIsInstance(c["weight"], int); self.assertGreater(c["weight"], 0)
                self.assertTrue(c["style"].strip() and c["brief"].strip())
            self.assertTrue(br.weighted_pool)

    def test_apex_is_shut_down_and_maasai_is_clean(self):
        self.assertFalse(self.b.APEX.active); self.assertTrue(self.b.MAASAI.active)
        m = self.b.MAASAI
        blob = " ".join([m.system_prompt, m.product_context, m.reply_guidance, json.dumps(m.theme),
                         " ".join(c["brief"] + c["style"] for c in m.categories),
                         " ".join(m.reply_keywords)]).lower()
        for banned in ("apex", "swarmsfall", "shieldclaw", "submit your deck", "maasai.vc"):
            self.assertNotIn(banned, blob, banned)
        # the only "marketplace" mentions must be the prohibition, not a claim
        for c in m.categories:
            self.assertNotIn("marketplace", c["brief"].lower())
        self.assertEqual(m.url, "")

    def test_get_brand(self):
        self.assertIs(self.b.get_brand("maasai"), self.b.MAASAI)
        with self.assertRaises(KeyError):
            self.b.get_brand("nope")


class TokenScriptTests(unittest.TestCase):
    SCRIPT = str(pathlib.Path(__file__).resolve().parent.parent / "get_twitter_tokens.py")

    def run_script(self, argv, env, handler=None, client=None):
        import contextlib, io, runpy
        out = io.StringIO(); code = None
        keys = ("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_OAUTH_STATE")
        old = {k: os.environ.get(k) for k in keys}
        for k in keys: os.environ.pop(k, None)
        os.environ.update(env)
        patches = []
        if handler: patches.append(mock.patch.object(tweepy, "OAuth1UserHandler", handler))
        if client: patches.append(mock.patch.object(tweepy, "Client", client))
        # keep the real .env from filling in missing keys
        patches.append(mock.patch("dotenv.load_dotenv", lambda *a, **k: False))
        try:
            for p in patches: p.start()
            with contextlib.redirect_stdout(out), mock.patch.object(sys, "argv", ["get_twitter_tokens.py"] + argv):
                try:
                    runpy.run_path(self.SCRIPT, run_name="__main__")
                except SystemExit as e:
                    code = e.code
        finally:
            for p in patches: p.stop()
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None: os.environ[k] = v
        return code, out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state.json")
        self.env = {"TWITTER_API_KEY": "k", "TWITTER_API_SECRET": "s", "TWITTER_OAUTH_STATE": self.state}

    def handler(self, access=("AT", "AS"), fail=False):
        h = mock.Mock()
        h.get_authorization_url.return_value = "https://api.twitter.com/oauth/authorize?oauth_token=RT"
        h.request_token = {"oauth_token": "RT", "oauth_token_secret": "RS"}
        if fail:
            h.get_access_token.side_effect = tweepy.TweepyException("Invalid oauth_verifier")
        else:
            h.get_access_token.return_value = access
        return mock.Mock(return_value=h)

    def test_missing_app_keys_exits_1(self):
        code, out = self.run_script([], {"TWITTER_OAUTH_STATE": self.state})
        self.assertEqual(code, 1); self.assertIn("not found", out)

    def test_step1_writes_private_state_and_prints_url(self):
        code, out = self.run_script([], self.env, handler=self.handler())
        self.assertIn("oauth_token=RT", out)
        self.assertEqual(json.load(open(self.state))["oauth_token_secret"], "RS")
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), "0o600")

    def test_step2_without_state_exits_1(self):
        code, out = self.run_script(["1234567"], self.env, handler=self.handler())
        self.assertEqual(code, 1); self.assertIn("no saved OAuth state", out)

    def test_step2_bad_pin_is_friendly_and_keeps_state(self):
        json.dump({"oauth_token": "RT", "oauth_token_secret": "RS"}, open(self.state, "w"))
        code, out = self.run_script(["000"], self.env, handler=self.handler(fail=True))
        self.assertEqual(code, 1); self.assertIn("PIN rejected", out)
        self.assertTrue(os.path.exists(self.state))

    def test_step2_success_prints_tokens_and_clears_state(self):
        json.dump({"oauth_token": "RT", "oauth_token_secret": "RS"}, open(self.state, "w"))
        client = mock.Mock(return_value=mock.Mock(get_me=mock.Mock(
            return_value=NS(data=NS(username="maasai_vc")))))
        code, out = self.run_script(["1234567"], self.env, handler=self.handler(), client=client)
        self.assertEqual(code, 0)
        self.assertIn("authorized as @maasai_vc", out)
        self.assertIn("MAASAI_ACCESS_TOKEN=AT", out); self.assertIn("MAASAI_ACCESS_SECRET=AS", out)
        self.assertFalse(os.path.exists(self.state))

    def test_step2_unverified_account_does_not_crash(self):
        json.dump({"oauth_token": "RT", "oauth_token_secret": "RS"}, open(self.state, "w"))
        client = mock.Mock(return_value=mock.Mock(get_me=mock.Mock(return_value=NS(data=None))))
        code, out = self.run_script(["1"], self.env, handler=self.handler(), client=client)
        self.assertEqual(code, 0); self.assertIn("(unverified)", out)


if __name__ == "__main__":
    unittest.main()
