import { FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

export function Unlock() {
  const { apiKey, saveKey } = useAuth();
  const navigate = useNavigate();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (apiKey) {
    return <Navigate to="/" replace />;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const next = value.trim();
    if (!next) {
      setError("Enter the OMS API key.");
      return;
    }
    setPending(true);
    setError(null);
    sessionStorage.setItem("oms_api_key", next);
    try {
      await api("/api/strategies");
      saveKey(next);
      navigate("/");
    } catch (err) {
      sessionStorage.removeItem("oms_api_key");
      setError(err instanceof Error ? err.message : "Could not reach the API");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="unlock">
      <form onSubmit={onSubmit}>
        <p className="eyebrow">SimpliEarn · Paper OMS</p>
        <h1>Open the desk</h1>
        <p className="lede">
          One key for this paper account. Strategy ids travel on each order, not in the key.
        </p>
        <label htmlFor="api-key">API key</label>
        <input
          id="api-key"
          autoFocus
          autoComplete="off"
          placeholder="dev-key"
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        {error ? <p className="form-error">{error}</p> : null}
        <button type="submit" disabled={pending}>
          {pending ? "Checking…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
