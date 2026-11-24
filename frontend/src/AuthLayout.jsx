import { PORTFOLIO_URL } from "./claimFields";

// Two-panel layout for every signed-out screen: sign in, choose your password,
// connecting / server waking up.
const POINTS = [
  ["Explainable fraud scores", "A trained model scores every claim and says which signals drove it."],
  ["Evidence, not guesses", "Each claim is compared with the most similar past claims and their outcomes."],
  ["People decide", "Investigators make the call; customers follow a clear, plain status."],
];

export default function AuthLayout({ children }) {
  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="auth-logo">
          <span className="brand-mark brand-mark-lg">CG</span>
          <div>
            <div className="auth-name">ClaimGuard</div>
            <div className="auth-sub">Insurance claims investigation</div>
          </div>
        </div>
        <h2 className="auth-headline">Fraud investigation,<br />explained.</h2>
        <ul className="auth-points">
          {POINTS.map(([title, text]) => (
            <li key={title}>
              <span className="auth-check" aria-hidden="true">✓</span>
              <div>
                <strong>{title}</strong>
                <span>{text}</span>
              </div>
            </li>
          ))}
        </ul>
        <div className="auth-foot">
          <div>Random Forest model · similar-claims evidence · Groq AI · human review</div>
          <div className="auth-credit">
            Built by Mukesh Kumar ·{" "}
            <a href={PORTFOLIO_URL} target="_blank" rel="noopener noreferrer">View portfolio ↗</a>
          </div>
        </div>
      </aside>
      <main className="auth-main">{children}</main>
    </div>
  );
}
