import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { ErrorText, Loading } from "../components/ui";
import { stateMatches } from "../signin";

/**
 * GitHub sends the browser here after installation (?code&installation_id&setup_action) and
 * after sign-in (?code&state).
 */
export function CallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = params.get("code");
    if (!code) {
      setError("GitHub did not return an authorization code.");
      return;
    }
    if (!stateMatches(params.get("state"))) {
      setError("This sign-in did not start in this browser. Sign in again from the home page.");
      return;
    }
    api
      .callback(code, params.get("installation_id"), params.get("setup_action"))
      .then(() => navigate("/repos", { replace: true }))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Sign-in failed"));
  }, [params, navigate]);
  return <div className="py-16">{error ? <ErrorText>{error}</ErrorText> : <Loading what="Finishing sign-in" />}</div>;
}
