import { useEffect, useState } from "react";
import { listClaims } from "./api";
import Sidebar from "./Sidebar";
import StatCards from "./StatCards";
import ClaimsList from "./ClaimsList";
import ClaimDetail from "./ClaimDetail";
import "./App.css";

export default function App() {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedClaimId, setSelectedClaimId] = useState(null);

  const refresh = () => {
    setLoading(true);
    setError(null);
    listClaims()
      .then(setClaims)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  return (
    <div className="shell">
      <Sidebar />

      <div className="main">
        <header className="topbar">
          <div>
            <h1>{selectedClaimId == null ? "Claims" : `Claim #${selectedClaimId}`}</h1>
            <p className="topbar-sub">
              {selectedClaimId == null
                ? "Review submitted claims and AI-generated fraud recommendations"
                : "Fraud analysis, retrieved evidence, and investigator sign-off"}
            </p>
          </div>
        </header>

        <main className="content">
          {selectedClaimId == null ? (
            <>
              {claims.length > 0 && <StatCards claims={claims} />}
              <ClaimsList
                claims={claims}
                loading={loading}
                error={error}
                onSelect={setSelectedClaimId}
                onRefresh={refresh}
              />
            </>
          ) : (
            <ClaimDetail
              claimId={selectedClaimId}
              onBack={() => setSelectedClaimId(null)}
              onFeedbackSubmitted={refresh}
            />
          )}
        </main>
      </div>
    </div>
  );
}
