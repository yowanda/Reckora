import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { PrivateRoute } from "@/components/PrivateRoute";
import { LoginPage } from "@/pages/Login";
import { MentionsPage } from "@/pages/Mentions";
import { NewInvestigationPage } from "@/pages/NewInvestigation";
import { NotFoundPage } from "@/pages/NotFound";
import { PinsPage } from "@/pages/PinsPage";
import { SubjectDetailPage } from "@/pages/SubjectDetail";
import { SubjectsListPage } from "@/pages/SubjectsList";
import { WatchingPage } from "@/pages/WatchingPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<PrivateRoute />}>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/subjects" replace />} />
          <Route path="subjects" element={<SubjectsListPage />} />
          <Route path="subjects/:subjectId" element={<SubjectDetailPage />} />
          <Route
            path="investigations/new"
            element={<NewInvestigationPage />}
          />
          <Route path="me/mentions" element={<MentionsPage />} />
          <Route path="me/pins" element={<PinsPage />} />
          <Route path="me/watching" element={<WatchingPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
