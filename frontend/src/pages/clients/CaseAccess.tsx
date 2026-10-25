import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiGet, apiPut, errorCode } from "../../api";
import { useMe } from "../../auth";
import type { VisaCase } from "../../contacts";

// Which employees see the case (вопрос 17): an employee sees only the cases given here,
// the rest of their submission groups and their clients (app/api/case_access.py).

type CaseEmployee = {
  id: string;
  full_name: string | null;
  email: string;
  is_active: boolean;
  has_modules: boolean;
  granted: boolean;
};

const accessKey = (caseId: string) => ["cases", caseId, "employees"] as const;

/** Checkboxes of the workspace's employees; only the specialist sees and changes them. */
export function CaseAccess({ visaCase: c }: { visaCase: VisaCase }) {
  const me = useMe();
  if (me.data?.role !== "specialist") return null;
  return <EmployeeChecks visaCase={c} />;
}

function EmployeeChecks({ visaCase: c }: { visaCase: VisaCase }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const employees = useQuery({
    queryKey: accessKey(c.id),
    queryFn: () => apiGet<CaseEmployee[]>(`/cases/${c.id}/employees`),
  });
  const save = useMutation({
    mutationFn: (ids: string[]) =>
      apiPut<CaseEmployee[]>(`/cases/${c.id}/employees`, { employee_ids: ids }),
    onSuccess: (data) => {
      queryClient.setQueryData(accessKey(c.id), data);
      void queryClient.invalidateQueries({ queryKey: ["employees"] });
    },
  });

  const list = employees.data ?? [];
  if (list.length === 0) return null;
  const granted = list.filter((e) => e.granted).map((e) => e.id);
  const toggle = (id: string, on: boolean) =>
    save.mutate(on ? [...granted, id] : granted.filter((g) => g !== id));

  return (
    <fieldset className="case-access">
      <legend className="muted">{t("caseAccess.title")}</legend>
      {list.map((e) => (
        <label key={e.id} className="checkbox">
          <input
            type="checkbox"
            checked={e.granted}
            disabled={save.isPending}
            onChange={(event) => toggle(e.id, event.target.checked)}
          />
          <span>{e.full_name || e.email}</span>
          {!e.is_active && <span className="chip st-gray">{t("caseAccess.blocked")}</span>}
          {e.is_active && !e.has_modules && (
            <span className="muted small">{t("caseAccess.noModules")}</span>
          )}
        </label>
      ))}
      <p className="muted small">
        {t(c.group_id ? "caseAccess.hintGroup" : "caseAccess.hint")}
      </p>
      {save.isError && (
        <div className="alert alert-error" role="alert">
          {t(`errors.${errorCode(save.error)}`, { defaultValue: t("errors.unknown") })}
        </div>
      )}
    </fieldset>
  );
}
