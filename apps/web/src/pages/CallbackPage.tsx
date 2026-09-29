import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { ErrorText, Loading } from "../components/ui";

/** GitHub sends the browser here after installation with ?code&installation_id&setup_action. */
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
    api
      .callback(code, params.get("installation_id"), params.get("setup_action"))
      .then(() => navigate("/repos", { replace: true }))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Sign-in failed"));
  }, [params, navigate]);
  return <div className="py-16">{error ? <ErrorText>{error}</ErrorText> : <Loading what="Finishing sign-in" />}</div>;
}
