import { NavLink, Outlet } from 'react-router-dom';

export function Layout() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            TC
          </span>
          <div>
            TenderCite<span>SOURCE-LINKED WORKSPACE</span>
          </div>
        </div>
        <nav aria-label="Primary navigation">
          <NavLink to="/profiles">Profiles</NavLink>
          <NavLink to="/opportunities">Opportunities</NavLink>
          <NavLink to="/tenders" end>
            Documents &amp; findings
          </NavLink>
          <NavLink to="/tenders/intake">Manual requirements</NavLink>
          <NavLink to="/tenders/workforce-snapshot/import">Import snapshot</NavLink>
          <NavLink to="/tenders/skill-mapping">Skill Mapping</NavLink>
          <NavLink to="/tenders/capability-report">Capability Matching</NavLink>
        </nav>
        <aside className="local-note" aria-label="Development environment">
          <strong>Local workspace</strong>
          <p>No authentication. Use permitted synthetic data only.</p>
        </aside>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>EVIDENCE · HUMAN REVIEW · MANUAL ASSESSMENT</span>
          <span className="local-pill">● Local only</span>
        </header>
        <main id="main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </>
  );
}
