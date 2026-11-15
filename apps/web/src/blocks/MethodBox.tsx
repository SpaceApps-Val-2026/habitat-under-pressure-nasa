import { useI18n } from "../i18n";
import { messages, type MessageKey } from "../i18n/messages";
import { referenced, Section, type BlockProps } from "./shared";

// The operation's name in words; an operation the locales do not name yet keeps its id.
export function operationKey(operation: string): MessageKey | null {
  return Object.hasOwn(messages.en.blocks.operation, operation) ? (`blocks.operation.${operation}` as MessageKey) : null;
}

export function MethodBox({ block, results }: BlockProps) {
  const { t } = useI18n();
  const methods = [...new Map(referenced(block, results).map((item) => [item.method.operation, item.method])).values()];

  return (
    <Section title={t("blocks.method")}>
      <ul className="flex flex-col gap-4">
        {methods.map((method) => {
          const key = operationKey(method.operation);
          return (
            <li key={method.operation} className="body">
              {key ? t(key) : method.operation}{" "}
              <span className="caption text-muted">
                · {t("blocks.version")} {method.version}
              </span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
