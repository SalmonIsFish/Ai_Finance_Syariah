import { BrowserRouter, Routes, Route } from "react-router-dom";
import Shell from "./components/layout/Shell";
import TheDesk from "./pages/TheDesk";
import PortfolioRisk from "./pages/PortfolioRisk";
import MarketScreening from "./pages/MarketScreening";
import TheLedger from "./pages/TheLedger";

export default function App() {
  return (
    <BrowserRouter basename="/dashboard">
      <Routes>
        <Route path="/" element={<Shell />}>
          <Route index element={<TheDesk />} />
          <Route path="risk" element={<PortfolioRisk />} />
          <Route path="market" element={<MarketScreening />} />
          <Route path="ledger" element={<TheLedger />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
