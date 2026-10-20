import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Shell } from "./Shell";
import { Account } from "./pages/Account";
import { OrderTicket } from "./pages/OrderTicket";
import { PositionDetail } from "./pages/PositionDetail";
import { Strategies } from "./pages/Strategies";
import { StrategyDetail } from "./pages/StrategyDetail";
import { Unlock } from "./pages/Unlock";
import { PointCloud } from "./PointCloud";

export function App() {
  return (
    <>
      <PointCloud />
      <Routes>
        <Route path="/unlock" element={<Unlock />} />
        <Route element={<Gate />}>
          <Route element={<Shell />}>
            <Route index element={<Account />} />
            <Route path="strategies" element={<Strategies />} />
            <Route path="strategies/:id" element={<StrategyDetail />} />
            <Route path="positions/:symbol" element={<PositionDetail />} />
            <Route path="order" element={<OrderTicket />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

function Gate() {
  const { apiKey } = useAuth();
  if (!apiKey) return <Navigate to="/unlock" replace />;
  return <Outlet />;
}
