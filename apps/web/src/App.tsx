import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AboutPage } from "./pages/AboutPage";
import { CallbackPage } from "./pages/CallbackPage";
import { ConnectPage } from "./pages/ConnectPage";
import { DiffPage } from "./pages/DiffPage";
import { ReportPage } from "./pages/ReportPage";
import { ReposPage } from "./pages/ReposPage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ConnectPage />} />
        <Route path="auth/github/callback" element={<CallbackPage />} />
        <Route path="repos" element={<ReposPage />} />
        <Route path="repos/:owner/:repo/diff" element={<DiffPage />} />
        <Route path="runs/:runId" element={<RunPage />} />
        <Route path="runs/:runId/report" element={<ReportPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<p>Not found.</p>} />
      </Route>
    </Routes>
  );
}

export default App;
