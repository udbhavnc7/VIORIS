from apps.desktop_agent.app.commands import handle, is_stop_request


class TestStop:
    def test_stop_alone(self):
        r = handle("stop")
        assert r.tool == "system.stop"
        assert r.action in ("stopped", "system.stop") or str(r.action) == "stopped"

    def test_vioris_stop(self):
        assert is_stop_request("vioris stop")
        assert handle("Vioris stop").tool == "system.stop"

    def test_not_stop(self):
        assert is_stop_request("stop") is True
        assert is_stop_request("tell me a joke") is False


class TestTime:
    def test_what_time(self):
        r = handle("what time is it")
        assert r.tool == "system.get_time"
        assert "time" in r.reply.lower()

    def test_tell_me_the_time(self):
        r = handle("tell me the time")
        assert r.tool == "system.get_time"


class TestOpenApp:
    def test_open_app(self):
        r = handle("open vs code")
        assert r.tool == "system.open_app"
        assert r.detail["app"] == "vs code"

    def test_open_app_without_name(self):
        r = handle("open")
        assert r.tool == "system.unknown"  # no app name -> not understood


class TestReminder:
    def test_reminder(self):
        r = handle("remind me to call mom at 6pm")
        assert r.tool == "system.set_reminder"
        assert r.detail["reminder"] == "call mom"
        assert r.detail["at"] == "6pm"


class TestUnknown:
    def test_unknown_falls_back(self):
        r = handle("sing me a song")
        assert r.tool == "system.unknown"
        assert "didn't catch" in r.reply
