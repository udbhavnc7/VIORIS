import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { clearSession, loadSession, type Session } from "./lib/api";
import { ShellWithDrawer } from "./components/AppShell";
import { usePrefs } from "./state/prefs";
import { DataProvider } from "./state/store";
import { Activity } from "./screens/Activity";
import { Approvals } from "./screens/Approvals";
import { Call } from "./screens/Call";
import { Connections } from "./screens/Connections";
import { Devices } from "./screens/Devices";
import { Memory } from "./screens/Memory";
import { Overview } from "./screens/Overview";
import { Pair } from "./screens/Pair";
import { Remote } from "./screens/Remote";
import { Settings } from "./screens/Settings";
import { Workflows } from "./screens/Workflows";

export function App() {
  const { prefs } = usePrefs();
  const [session, setSession] = useState<Session | null>(() => loadSession());

  const signOut = useCallback(() => {
    clearSession();
    setSession(null);
  }, []);

  useEffect(() => {
    // Any 401 from the gateway means the device key is gone or expired. Drop
    // the local session and return to the pairing gate instead of retrying.
    const onUnauthorized = () => signOut();
    window.addEventListener("vioris:unauthorized", onUnauthorized);
    return () => window.removeEventListener("vioris:unauthorized", onUnauthorized);
  }, [signOut]);

  if (!session) {
    return <Pair onPaired={setSession} />;
  }

  return (
    <DataProvider autoRefresh={prefs.autoRefresh}>
      <ShellWithDrawer onSignOut={signOut}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/memory" element={<Memory />} />
          <Route path="/connections" element={<Connections />} />
          <Route path="/remote" element={<Remote />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/call" element={<Call />} />
          <Route path="/settings" element={<Settings onSignOut={signOut} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ShellWithDrawer>
    </DataProvider>
  );
}
