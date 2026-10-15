import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiGet } from "../api";

export type ModuleInfo = { key: string; block: string; stage: number };

export function useModules() {
  return useQuery({
    queryKey: ["modules"],
    queryFn: () => apiGet<ModuleInfo[]>("/modules"),
    staleTime: Infinity,
  });
}

/** Checkboxes for modules grouped by block; `allowed` limits the choice (e.g. for employees). */
export function ModulePicker({
  value,
  onChange,
  allowed,
}: {
  value: string[];
  onChange: (modules: string[]) => void;
  allowed?: string[];
}) {
  const { t } = useTranslation();
  const modules = useModules();
  if (!modules.data) return <p className="muted">{t("common.loading")}</p>;

  const shown = modules.data.filter((m) => !allowed || allowed.includes(m.key));
  if (shown.length === 0) return <p className="muted">{t("modules.none")}</p>;
  const blocks = [...new Set(shown.map((m) => m.block))];
  const toggle = (key: string, on: boolean) =>
    onChange(on ? [...value, key] : value.filter((k) => k !== key));

  return (
    <div className="module-picker">
      {blocks.map((block) => (
        <fieldset key={block} className="module-block">
          <legend>{t(`modules.block.${block}`)}</legend>
          {shown
            .filter((m) => m.block === block)
            .map((m) => (
              <label key={m.key} className="checkbox">
                <input
                  type="checkbox"
                  checked={value.includes(m.key)}
                  onChange={(e) => toggle(m.key, e.target.checked)}
                />
                <span>{t(`modules.name.${m.key}`)}</span>
                <span className="chip st-gray">{t("modules.stage", { stage: m.stage })}</span>
              </label>
            ))}
        </fieldset>
      ))}
    </div>
  );
}
