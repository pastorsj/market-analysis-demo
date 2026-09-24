import { useCallback, useId, useLayoutEffect, useRef } from "react";
import type { Citation } from "../api/types";

const FOCUSABLE = "a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])";

function focusableElements(dialog: HTMLElement | null): HTMLElement[] {
  if (!dialog) return [];
  return [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((element) => (
    !element.hasAttribute("hidden") && element.getAttribute("aria-hidden") !== "true"
  ));
}

export function EvidenceDrawer({ citation, onClose }: { citation: Citation | null; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null), dialog = useRef<HTMLElement>(null), opener = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose), titleId = useId(), citationId = citation?.citation_id ?? null;
  const requestClose = useCallback(() => onCloseRef.current(), []);

  useLayoutEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useLayoutEffect(() => {
    if (!citationId) return;
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        requestClose();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusableElements(dialog.current), first = elements[0], last = elements.at(-1);
      if (!first || !last) {
        event.preventDefault();
        dialog.current?.focus();
        return;
      }
      const activeElement = document.activeElement, outside = !dialog.current?.contains(activeElement);
      if ((event.shiftKey && (activeElement === first || outside)) || (!event.shiftKey && (activeElement === last || outside))) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
      event.stopPropagation();
    };
    const containFocus = (event: FocusEvent) => {
      const target = event.target;
      if (target instanceof Node && dialog.current && !dialog.current.contains(target)) {
        (focusableElements(dialog.current)[0] ?? dialog.current).focus();
      }
    };

    window.addEventListener("keydown", handleKey, true);
    document.addEventListener("focusin", containFocus);
    close.current?.focus();
    return () => {
      window.removeEventListener("keydown", handleKey, true);
      document.removeEventListener("focusin", containFocus);
      const invokingElement = opener.current;
      opener.current = null;
      if (invokingElement?.isConnected) invokingElement.focus();
    };
  }, [citationId, requestClose]);

  if (!citation) return null;

  return (
    <div className="scrim evidence-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <aside ref={dialog} className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="drawer-header">
          <div>
            <span className="source-type">{citation.source_type}</span>
            <span className="time-badge">Available by cutoff</span>
          </div>
          <button ref={close} className="close" onClick={requestClose} aria-label="Close evidence">×</button>
        </header>
        <h2 id={titleId}>{citation.title}</h2>
        <blockquote>{citation.excerpt}</blockquote>
        <dl>
          <div><dt>Published</dt><dd>{new Date(citation.published_at).toLocaleString()}</dd></div>
          <div><dt>Available</dt><dd>{new Date(citation.available_at).toLocaleString()}</dd></div>
          <div><dt>Citation ID</dt><dd className="mono">{citation.citation_id}</dd></div>
        </dl>
        {citation.url && <a href={citation.url} target="_blank" rel="noreferrer">Open evidence source <span aria-hidden="true">↗</span></a>}
      </aside>
    </div>
  );
}
