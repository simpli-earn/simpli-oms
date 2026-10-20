import { NavLink, Outlet } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth";

export function Shell() {
  const { clearKey } = useAuth();

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span>SimpliEarn</span>
          <small>Paper OMS</small>
        </div>
        <nav>
          <NavLink to="/" end>
            Account
          </NavLink>
          <NavLink to="/strategies">Strategies</NavLink>
          <NavLink to="/order">New order</NavLink>
        </nav>
        <div className="rail-foot">
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
          <button type="button" onClick={clearKey}>
            Lock
          </button>
        </div>
      </aside>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

export function PageHeader({
  title,
  lede,
  children,
}: {
  title: string;
  lede?: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {lede ? <p>{lede}</p> : null}
      </div>
      {children}
    </header>
  );
}

export function Banner({ children }: { children: ReactNode }) {
  return <div className="banner">{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
