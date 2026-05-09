import { Navigate, Outlet, useLocation } from "react-router-dom";

import { Spinner } from "@/components/Spinner";
import { useAuth } from "@/lib/auth";

export function PrivateRoute() {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (state.status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <Outlet />;
}
