import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AppealsLabel, BookingLabel, OperatorLabel } from "../../components/RuleLabels";
import {
  DEFAULT_SUBMISSION_CODE,
  byId,
  useCountries,
  useLocalName,
  useOperators,
  useRules,
} from "../../reference";

/** Workspace: how to get an appointment for each country, read-only (the admin keeps it). */
export function CountriesPage() {
  const { t } = useTranslation();
  const name = useLocalName();
  const countries = useCountries();
  const operators = useOperators();
  const allRules = useRules(false);
  const [submissionId, setSubmissionId] = useState("");

  const countryById = useMemo(() => byId(countries.data), [countries.data]);
  const operatorById = useMemo(() => byId(operators.data), [operators.data]);
  // Only countries where applications are taken in this platform's reference.
  const submissions = useMemo(() => {
    const ids = new Set((allRules.data ?? []).map((r) => r.submission_id));
    return [...ids]
      .map((id) => countryById.get(id))
      .filter((c) => c !== undefined)
      .sort((a, b) => name.country(a).localeCompare(name.country(b)));
  }, [allRules.data, countryById, name]);

  useEffect(() => {
    if (submissionId || submissions.length === 0) return;
    const home = submissions.find((c) => c.code === DEFAULT_SUBMISSION_CODE) ?? submissions[0];
    setSubmissionId(home.id);
  }, [submissions, submissionId]);

  const rules = (allRules.data ?? [])
    .filter((r) => r.submission_id === submissionId)
    .sort((a, b) =>
      name
        .country(countryById.get(a.destination_id))
        .localeCompare(name.country(countryById.get(b.destination_id))),
    );

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{t("reference.countriesTitle")}</h1>
          <div className="muted">{t("reference.subtitleWorkspace")}</div>
        </div>
      </header>

      {submissions.length > 1 && (
        <div className="toolbar">
          <label className="field field-inline">
            <span>{t("reference.submissionCountry")}</span>
            <select value={submissionId} onChange={(e) => setSubmissionId(e.target.value)}>
              {submissions.map((c) => (
                <option key={c.id} value={c.id}>
                  {name.country(c)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <section className="card">
        {allRules.isPending && <p className="muted">{t("common.loading")}</p>}
        {allRules.isError && <p className="alert alert-error">{t("errors.unknown")}</p>}
        {allRules.data && rules.length === 0 && <p className="muted">{t("reference.noRules")}</p>}
        {rules.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("reference.colDestination")}</th>
                  <th>{t("reference.colBooking")}</th>
                  <th>{t("reference.colOperator")}</th>
                  <th>{t("reference.colIdentifier")}</th>
                  <th>{t("reference.colAppeals")}</th>
                  <th>{t("reference.colCities")}</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.id}>
                    <td className="nowrap">
                      {name.country(countryById.get(rule.destination_id))}
                      {!rule.is_verified && (
                        <div>
                          <span className="chip st-orange">{t("reference.unverified")}</span>
                        </div>
                      )}
                    </td>
                    <td>
                      <BookingLabel rule={rule} />
                    </td>
                    <td>
                      <OperatorLabel
                        rule={rule}
                        operator={operatorById.get(rule.vac_operator_id ?? "")}
                        represented={countryById.get(rule.represented_by_id ?? "")}
                      />
                    </td>
                    <td className="muted">
                      {rule.identifier_type ? t(`reference.identifier.${rule.identifier_type}`) : "—"}
                    </td>
                    <td className="muted">
                      <AppealsLabel rule={rule} />
                    </td>
                    <td className="muted">
                      {rule.visa_centers.map((v) => name.city(v)).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="muted small">{t("reference.workspaceHint")}</p>
    </div>
  );
}
