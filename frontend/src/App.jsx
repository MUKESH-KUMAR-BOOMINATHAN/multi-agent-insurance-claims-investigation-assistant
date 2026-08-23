import { useEffect, useState } from "react";
import { listClaims } from "./api";
import Sidebar from "./Sidebar";
import StatCards from "./StatCards";
import ClaimsList from "./ClaimsList";
import ClaimDetail from "./ClaimDetail";
import ClaimForm from "./ClaimForm";
import "./App.css";

export default function App() {
  const [claims, setClaims] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // view: "list" | "detail" | "new"
  const [view, setView] = useState("list");
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

  const openDetail = (id) => {
    setSelectedClaimId(id);
    setView("detail");
  };

  const handleSubmitted = (newClaimId) => {
    refresh();
    openDetail(newClaimId);
  };

  const titles = {
    list: ["Claims", "Review submitted claims and AI-generated fraud recommendations"],
    new: ["Submit New Claim", "Fill in the claim details to run the investigation pipeline"],
    detail: [`Claim #${selectedClaimId}`, "Fraud analysis, retrieved evidence, and investigator sign-off"],
  };
  const [title, subtitle] = titles[view];

  return (
    <div className="shell">
      <Sidebar />

      <div className="main">
        <header className="topbar">
          <div>
            <h1>{title}</h1>
            <p className="topbar-sub">{subtitle}</p>
          </div>
          {view === "list" && (
            <button className="primary-btn" onClick={() => setView("new")}>
              + New Claim
            </button>
          )}
        </header>

        <main className="content">
          {view === "list" && (
            <>
              {claims.length > 0 && <StatCards claims={claims} />}
              <ClaimsList
                claims={claims}
                loading={loading}
                error={error}
                onSelect={openDetail}
                onRefresh={refresh}
              />
            </>
          )}

          {view === "new" && (
            <ClaimForm onSubmitted={handleSubmitted} onCancel={() => setView("list")} />
          )}

          {view === "detail" && (
            <ClaimDetail
              claimId={selectedClaimId}
              onBack={() => setView("list")}
              onFeedbackSubmitted={refresh}
            />
          )}
        </main>
      </div>
    </div>
  );
}
