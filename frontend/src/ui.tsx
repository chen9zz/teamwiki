import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, CheckCircle2, LoaderCircle, X, BookOpen } from 'lucide-react';
import { message } from './api';

export function Loading({ text = '正在加载…' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={17} className="spinner" />
      {text}
    </div>
  );
}
export function ErrorNotice({ error }: { error: unknown }) {
  return error ? (
    <div className="error" role="alert">
      {message(error)}
    </div>
  ) : null;
}
export function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <BookOpen size={29} strokeWidth={1.3} />
      </div>
      <h2>{title}</h2>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
        ) || [],
      );
    (focusable()[1] || focusable()[0])?.focus();
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const elements = focusable();
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('keydown', key);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        className={`modal ${wide ? 'wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="关闭对话框">
            <X size={18} />
          </button>
        </header>
        <div className="modal-content">{children}</div>
      </div>
    </div>
  );
}
interface Notification {
  text: string;
  failed: boolean;
}
const ToastContext = createContext<(text: string, failed?: boolean) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notification | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  function notify(text: string, failed = false) {
    clearTimeout(timer.current);
    setNotice({ text, failed });
    timer.current = setTimeout(() => setNotice(null), 4500);
  }
  return (
    <ToastContext.Provider value={notify}>
      {children}
      {notice && (
        <div
          className={`toast ${notice.failed ? 'failure' : ''}`}
          role={notice.failed ? 'alert' : 'status'}
        >
          {notice.failed ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          <span>{notice.text}</span>
          <button className="icon-btn" onClick={() => setNotice(null)} aria-label="关闭通知">
            <X size={13} />
          </button>
        </div>
      )}
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);
