import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronDown,
  Clock3,
  Code2,
  CornerDownRight,
  Edit3,
  Eye,
  FilePlus2,
  FileText,
  GitBranch,
  History,
  Info,
  Link2,
  List,
  MoreHorizontal,
  PencilLine,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Tag,
  Trash2,
  X,
} from 'lucide-react';
import { api, ApiError, download, message } from './api';
import { canWrite, date, headingList } from './lib';
import type {
  Links,
  Page,
  PageSummary,
  Revision,
  RevisionLinks,
  User,
  Workspace,
} from './types';
import { Empty, ErrorNotice, Loading, Modal, useToast } from './ui';
import { Markdown } from './Markdown';
import { PAGE_BATCH_SIZE, usePageTree } from './pagination';

const MarkdownEditor = lazy(() => import('./MarkdownEditor'));

interface Draft {
  title: string;
  content: string;
  version: number;
}
function readDraft(key: string): Draft | null {
  try {
    const item = JSON.parse(sessionStorage.getItem(key) || 'null');
    return item &&
      typeof item.title === 'string' &&
      typeof item.content === 'string' &&
      Number.isInteger(item.version)
      ? item
      : null;
  } catch {
    return null;
  }
}
function Versions({
  page,
  writable,
  onClose,
  onRestore,
}: {
  page: Page;
  writable: boolean;
  onClose: () => void;
  onRestore: (page: Page) => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const history = useInfiniteQuery({
    queryKey: ['revisions', page.id, page.version],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      api<Revision[]>(
        `/workspaces/${page.workspace_id}/pages/${page.id}/revisions?limit=${PAGE_BATCH_SIZE}&offset=${pageParam}`,
        { signal },
      ),
    getNextPageParam: (last, batches) =>
      last.length === PAGE_BATCH_SIZE ? batches.length * PAGE_BATCH_SIZE : undefined,
  });
  const detail = useQuery({
    queryKey: ['revision', page.id, selected],
    queryFn: () =>
      api<Revision>(
        `/workspaces/${page.workspace_id}/pages/${page.id}/revisions/${selected}`,
      ),
    enabled: selected !== null,
  });
  const historicalLinks = useQuery({
    queryKey: ['revision-links', page.workspace_id, page.id, selected],
    queryFn: ({ signal }) =>
      api<RevisionLinks>(
        `/workspaces/${page.workspace_id}/pages/${page.id}/revisions/${selected}/links`,
        { signal },
      ),
    enabled: selected !== null,
  });
  const restore = useMutation({
    mutationFn: (version: number) =>
      api<Page>(
        `/workspaces/${page.workspace_id}/pages/${page.id}/revisions/${version}/restore`,
        {
          method: 'POST',
          body: { version: page.version },
        },
      ),
    onSuccess: (restored) => {
      onRestore(restored);
      onClose();
    },
  });
  return (
    <Modal title="文档历史版本" onClose={onClose} wide>
      <p className="modal-description">
        每次保存都会保留完整快照。恢复操作会创建一个新版本，历史记录始终保留。
      </p>
      {history.isPending ? <Loading /> : <ErrorNotice error={history.error} />}
      <div className="revision-list">
        {history.data &&
          [...history.data.pages.flat()]
            .sort((a, b) => b.version - a.version)
            .map((revision) => (
              <div
                key={revision.version}
                className={`revision-row ${selected === revision.version ? 'selected' : ''}`}
              >
                <History size={15} />
                <button
                  style={{ textAlign: 'left', flex: 1 }}
                  onClick={() => setSelected(revision.version)}
                >
                  版本 {revision.version}{' '}
                  {revision.version === page.version && <span className="badge">当前</span>}
                  <small>
                    {revision.title} · {date(revision.created_at, true)}
                  </small>
                </button>
                <button
                  className="button small"
                  onClick={() => setSelected(revision.version)}
                >
                  查看
                </button>
                {writable && revision.version !== page.version && (
                  <button
                    className="button small"
                    disabled={restore.isPending}
                    onClick={() => setSelected(revision.version)}
                  >
                    选择恢复
                  </button>
                )}
              </div>
            ))}
      </div>
      {history.hasNextPage && (
        <button
          className="button"
          aria-label="加载更早版本"
          disabled={history.isFetchingNextPage}
          onClick={() => history.fetchNextPage()}
        >
          加载更早版本
        </button>
      )}
      {detail.isFetching ? (
        <Loading />
      ) : (
        detail.data && (
          <div className="revision-preview">
            <div style={{ fontSize: 10, color: '#8da27c', marginBottom: 15 }}>
              版本 {detail.data.version} · {detail.data.title}
            </div>
            <p style={{ fontSize: 10, color: '#8da27c' }}>链接按当前空间解析</p>
            {historicalLinks.isPending ? (
              <Loading text="正在解析历史链接…" />
            ) : (
              <Markdown
                content={detail.data.content}
                links={historicalLinks.data?.outgoing || []}
                wid={page.workspace_id}
              />
            )}
            {writable && detail.data.version !== page.version && (
              <div className="modal-actions">
                <button
                  className="button primary"
                  onClick={() => restore.mutate(detail.data.version)}
                  disabled={restore.isPending}
                >
                  <RefreshCw size={13} />
                  {restore.isPending ? '正在恢复…' : `确认恢复到版本 ${detail.data.version}`}
                </button>
              </div>
            )}
          </div>
        )
      )}
      <ErrorNotice error={detail.error || historicalLinks.error || restore.error} />
    </Modal>
  );
}
export function PageEditor({
  pid,
  workspace,
  pages,
  user,
  onManage,
  onCreateChild,
}: {
  pid: string;
  workspace: Workspace;
  pages: PageSummary[];
  user: User;
  onManage: (page: Page) => void;
  onCreateChild: (page: Page) => void;
}) {
  const query = useQuery({
    queryKey: ['page', pid],
    queryFn: () => api<Page>(`/workspaces/${workspace.id}/pages/${pid}`),
  });
  if (query.isPending)
    return (
      <div className="document-main">
        <Loading text="正在打开文档…" />
      </div>
    );
  if (query.error || !query.data)
    return (
      <Empty title="无法打开这篇文档" description="文档可能已删除，或者你没有访问权限。">
        <ErrorNotice error={query.error} />
        <button className="button" onClick={() => query.refetch()} style={{ marginTop: 15 }}>
          重试
        </button>
      </Empty>
    );
  if (query.data.workspace_id !== workspace.id)
    return <Empty title="文档不在当前空间" description="请从左侧目录选择当前空间的文档。" />;
  if (query.data.deleted_at)
    return (
      <Empty
        title="这篇文档已移入回收站"
        description="有编辑权限的成员可以从回收站中恢复这篇文档。"
      >
        <Link className="button" to={`/w/${workspace.id}/trash`}>
          打开回收站
        </Link>
      </Empty>
    );
  return (
    <EditorReady
      key={pid}
      page={query.data}
      workspace={workspace}
      pages={pages}
      user={user}
      onManage={onManage}
      onCreateChild={onCreateChild}
    />
  );
}
function EditorReady({
  page,
  workspace,
  pages,
  user,
  onManage,
  onCreateChild,
}: {
  page: Page;
  workspace: Workspace;
  pages: PageSummary[];
  user: User;
  onManage: (page: Page) => void;
  onCreateChild: (page: Page) => void;
}) {
  const key = `teamwiki-draft:${user.id}:${page.id}`;
  const restoredDraft = useMemo(() => readDraft(key), [key]);
  const [draft, setDraft] = useState<Draft>(
    restoredDraft || { title: page.title, content: page.content, version: page.version },
  );
  const [baseline, setBaseline] = useState({ title: page.title, content: page.content });
  const [mode, setMode] = useState<'read' | 'edit'>(restoredDraft ? 'edit' : 'read');
  const [conflict, setConflict] = useState<Page | null>(null);
  const [history, setHistory] = useState(false);
  const [remove, setRemove] = useState(false);
  const [more, setMore] = useState(false);
  const client = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const writable = canWrite(workspace.role);
  const dirty = draft.title !== baseline.title || draft.content !== baseline.content;
  const links = useQuery({
    queryKey: ['links', page.id, page.version],
    queryFn: () => api<Links>(`/workspaces/${page.workspace_id}/pages/${page.id}/links`),
  });
  const headings = headingList(mode === 'edit' ? draft.content : page.content);
  const chars = (mode === 'edit' ? draft.content : page.content).length;
  const reading = Math.max(1, Math.ceil(chars / 450));
  const scrollRef = useRef<HTMLDivElement>(null);
  function accepted(saved: Page) {
    setDraft({ title: saved.title, content: saved.content, version: saved.version });
    setBaseline({ title: saved.title, content: saved.content });
    setConflict(null);
    sessionStorage.removeItem(key);
    client.setQueryData(['page', page.id], saved);
    client.invalidateQueries({ queryKey: ['pages', workspace.id] });
    client.invalidateQueries({ queryKey: ['links'] });
    client.invalidateQueries({ queryKey: ['revisions', page.id] });
    client.invalidateQueries({ queryKey: ['graph', workspace.id] });
    client.invalidateQueries({ queryKey: ['search', workspace.id] });
  }
  const save = useMutation({
    mutationFn: () =>
      api<Page>(`/workspaces/${page.workspace_id}/pages/${page.id}`, {
        method: 'PATCH',
        body: { version: draft.version, title: draft.title.trim(), content: draft.content },
      }),
    onSuccess: (saved) => {
      accepted(saved);
      toast('文档已保存，新的知识已连接');
    },
    onError: (error) => {
      if (error instanceof ApiError && error.conflict) setConflict(error.conflict.current);
    },
  });
  const deletion = useMutation({
    mutationFn: () =>
      api<Page>(`/workspaces/${page.workspace_id}/pages/${page.id}?version=${page.version}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['pages', workspace.id] });
      client.invalidateQueries({ queryKey: ['trash', workspace.id] });
      client.invalidateQueries({ queryKey: ['graph', workspace.id] });
      sessionStorage.removeItem(key);
      toast('文档已移入回收站');
      navigate(`/w/${workspace.id}`);
    },
  });
  useEffect(() => {
    if (dirty) {
      try {
        sessionStorage.setItem(key, JSON.stringify(draft));
      } catch {
        /* The current editor still retains the draft if browser storage is full. */
      }
    } else sessionStorage.removeItem(key);
  }, [draft, dirty, key]);
  useEffect(() => {
    if (!dirty && page.version !== draft.version) {
      setDraft({ title: page.title, content: page.content, version: page.version });
      setBaseline({ title: page.title, content: page.content });
    }
  }, [page.version, page.title, page.content, dirty, draft.version]);
  useEffect(() => {
    function leave(event: BeforeUnloadEvent) {
      if (dirty) {
        event.preventDefault();
      }
    }
    window.addEventListener('beforeunload', leave);
    return () => window.removeEventListener('beforeunload', leave);
  }, [dirty]);
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (writable && dirty && !save.isPending && draft.title.trim()) save.mutate();
      }
    }
    document.addEventListener('keydown', shortcut);
    return () => document.removeEventListener('keydown', shortcut);
  }, [writable, dirty, save.isPending, draft]);
  async function exportPage() {
    try {
      await download(
        `/workspaces/${page.workspace_id}/pages/${page.id}/export`,
        `${page.title}.md`,
      );
      toast('Markdown 文档已导出');
    } catch (error) {
      toast(message(error), true);
    }
  }
  function serverCopy() {
    if (!conflict) return;
    accepted(conflict);
    setMode('read');
    toast('已载入服务器版本');
  }
  function keepDraft() {
    if (!conflict) return;
    const current = conflict;
    setDraft((previous) => ({ ...previous, version: current.version }));
    setBaseline({ title: current.title, content: current.content });
    client.setQueryData(['page', page.id], current);
    setConflict(null);
    save.reset();
    toast('草稿已保留。请合并修改后再次保存。');
  }
  const outgoing = links.data?.outgoing || [];
  const backlinks = links.data?.backlinks || [];
  const stateLabels: Record<string, string> = {
    unresolved: '未找到',
    ambiguous: '同名文档',
    deleted: '已删除',
  };
  return (
    <>
      <div className="document-main">
        <div className="document-scroll" ref={scrollRef}>
          <article className="document-width">
            <div className="doc-eyebrow">
              <span
                style={{ width: 5, height: 5, background: '#92ac7c', borderRadius: '50%' }}
              />
              TEAM KNOWLEDGE BASE{' '}
              <span style={{ letterSpacing: 0, marginLeft: 'auto', fontSize: 9 }}>
                文档 /{' '}
                {page.parent_id
                  ? pages.find((item) => item.id === page.parent_id)?.title || '子页面'
                  : '知识库'}
              </span>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div className="doc-icon">
                <FileText size={24} strokeWidth={1.4} />
              </div>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                  position: 'relative',
                }}
              >
                <button
                  className="icon-btn"
                  onClick={() => setHistory(true)}
                  title="历史版本"
                  aria-label="历史版本"
                >
                  <History size={16} />
                </button>
                <button
                  className="icon-btn"
                  onClick={exportPage}
                  title="导出 Markdown"
                  aria-label="导出 Markdown"
                >
                  <ArrowDownToLine size={16} />
                </button>
                <button
                  className="icon-btn"
                  onClick={() => setMore(!more)}
                  title="更多操作"
                  aria-label="更多操作"
                  aria-expanded={more}
                >
                  <MoreHorizontal size={18} />
                </button>
                {more && (
                  <div
                    style={{
                      position: 'absolute',
                      right: 0,
                      top: 35,
                      width: 170,
                      padding: 6,
                      background: '#fff',
                      border: '1px solid #e1e8d8',
                      borderRadius: 7,
                      boxShadow: '0 8px 25px #233d2615',
                      zIndex: 5,
                    }}
                  >
                    {writable && (
                      <>
                        <button
                          className="button"
                          style={{ border: 0, width: '100%', justifyContent: 'flex-start' }}
                          onClick={() => {
                            setMore(false);
                            onManage(page);
                          }}
                        >
                          <PencilLine size={13} />
                          文档设置 / 移动
                        </button>
                        <button
                          className="button"
                          style={{ border: 0, width: '100%', justifyContent: 'flex-start' }}
                          onClick={() => {
                            setMore(false);
                            onCreateChild(page);
                          }}
                        >
                          <FilePlus2 size={13} />
                          添加子文档
                        </button>
                        <button
                          className="button danger"
                          style={{ border: 0, width: '100%', justifyContent: 'flex-start' }}
                          onClick={() => {
                            setMore(false);
                            setRemove(true);
                          }}
                        >
                          <Trash2 size={13} />
                          移入回收站
                        </button>
                      </>
                    )}
                    <button
                      className="button"
                      style={{ border: 0, width: '100%', justifyContent: 'flex-start' }}
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(window.location.href);
                          toast('文档链接已复制');
                        } catch {
                          toast('无法访问剪贴板，请复制浏览器地址。', true);
                        }
                        setMore(false);
                      }}
                    >
                      <Link2 size={13} />
                      复制文档链接
                    </button>
                  </div>
                )}
              </div>
            </div>
            {mode === 'edit' ? (
              <input
                className="editor-title"
                aria-label="文档标题"
                value={draft.title}
                maxLength={240}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                placeholder="无标题文档"
              />
            ) : (
              <h1 className="page-title">{page.title}</h1>
            )}
            <div className="doc-meta">
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span className="mini-avatar">{user.name.slice(0, 1)}</span>
                {page.updated_by === user.id ? user.name : '团队成员'}
              </span>
              <span className="separator" />
              <span>{date(page.updated_at, true)} 更新</span>
              <span className="separator" />
              <span>约 {reading} 分钟阅读</span>
              <span className="badge">版本 {page.version}</span>
            </div>
            <div className="doc-tabs">
              <button
                className={`doc-tab ${mode === 'read' ? 'active' : ''}`}
                onClick={() => setMode('read')}
              >
                <Eye size={13} />
                阅读
              </button>
              {writable && (
                <button
                  className={`doc-tab ${mode === 'edit' ? 'active' : ''}`}
                  onClick={() => setMode('edit')}
                >
                  <Edit3 size={12} />
                  编辑文档
                </button>
              )}
              <button className="doc-tab" onClick={() => setHistory(true)}>
                <Clock3 size={12} />
                历史
              </button>
              <div className="doc-status">
                {dirty ? (
                  <>
                    <span
                      style={{ width: 4, height: 4, borderRadius: 5, background: '#c4a66c' }}
                    />
                    有未保存修改
                  </>
                ) : (
                  <>
                    <CheckCheck size={12} />
                    已同步至空间
                  </>
                )}
              </div>
            </div>
            {dirty && mode === 'read' && (
              <div className="version-warning">
                <PencilLine size={14} />
                你的修改仍在编辑器中，阅读视图显示已发布版本。
                <button
                  style={{ marginLeft: 'auto', textDecoration: 'underline' }}
                  onClick={() => setMode('edit')}
                >
                  继续编辑
                </button>
              </div>
            )}
            {draft.version < page.version && dirty && (
              <div className="version-warning">
                <Info size={14} />
                文档已有较新版本（v{page.version}）。保存时将比较版本，草稿会保留。
                <button
                  style={{ textDecoration: 'underline', marginLeft: 'auto' }}
                  onClick={() => setConflict(page)}
                >
                  查看差异
                </button>
              </div>
            )}
            {mode === 'read' ? (
              page.content ? (
                <Markdown
                  content={page.content}
                  pages={pages}
                  links={outgoing}
                  wid={workspace.id}
                />
              ) : (
                <Empty
                  title="留白，等待一个好想法"
                  description={
                    writable
                      ? '点击编辑文档，开始记录团队的第一份知识。'
                      : '这篇文档暂时还没有内容。'
                  }
                >
                  {writable && (
                    <button className="button" onClick={() => setMode('edit')}>
                      <PencilLine size={13} />
                      开始写作
                    </button>
                  )}
                </Empty>
              )
            ) : (
              <>
                <div className="editor-wrap">
                  <div className="editor-toolbar">
                    <Code2 size={13} />
                    Markdown <span>输入 [[文档标题]] 连接知识 · 支持 GFM</span>
                  </div>
                  <Suspense fallback={<Loading text="正在准备编辑器…" />}>
                    <MarkdownEditor
                      value={draft.content}
                      onChange={(content) =>
                        setDraft((previous) => ({ ...previous, content }))
                      }
                    />
                  </Suspense>
                </div>
                <div className="editor-footer">
                  <span>
                    {chars.toLocaleString()} 字符 ·{' '}
                    {dirty ? '草稿已保留在当前浏览器' : '所有修改已保存'}
                  </span>
                  <button
                    className="button primary"
                    onClick={() => save.mutate()}
                    disabled={!dirty || !draft.title.trim() || save.isPending}
                  >
                    <Save size={13} />
                    {save.isPending ? '正在保存…' : '保存文档'}
                    <kbd style={{ fontSize: 9, opacity: 0.65, marginLeft: 4 }}>⌘ S</kbd>
                  </button>
                </div>
                <div style={{ marginTop: 15 }}>
                  <ErrorNotice error={save.error} />
                </div>
              </>
            )}
            {mode === 'read' && <ChildPages wid={workspace.id} pid={page.id} />}
          </article>
        </div>
        <footer className="doc-footer">
          <div>
            <ShieldCheck size={11} />
            权限保护 · 内容安全保存在团队空间
          </div>
          <div>
            <kbd>⌘ K</kbd> 搜索知识 &nbsp; <kbd>⌘ S</kbd> 保存修改
          </div>
        </footer>
      </div>
      <aside className="right-rail">
        <h2 className="rail-label">
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <List size={12} />
            页面目录
          </span>
        </h2>
        {headings.length ? (
          <nav className="outline">
            {headings.map((heading) => (
              <button
                key={heading.id}
                style={{ paddingLeft: 13 + Math.max(0, heading.depth - 2) * 9 }}
                onClick={() => {
                  if (mode === 'edit') setMode('read');
                  window.setTimeout(
                    () =>
                      document
                        .getElementById(heading.id)
                        ?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
                    30,
                  );
                }}
              >
                {heading.text}
              </button>
            ))}
          </nav>
        ) : (
          <p className="rail-hint">
            用 Markdown 标题组织内容，
            <br />
            目录会自动出现在这里。
          </p>
        )}
        <div className="rail-divider" />
        <h2 className="rail-label">
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <GitBranch size={12} />
            反向链接
          </span>
          <span className="badge neutral">{backlinks.length}</span>
        </h2>
        {links.isPending ? (
          <p className="rail-hint">正在查找关联文档…</p>
        ) : backlinks.length ? (
          backlinks.map((link) => {
            const source = pages.find((item) => item.id === link.source_id);
            return (
              <Link
                key={link.id}
                className="backlink"
                to={`/w/${workspace.id}/p/${link.source_id}`}
              >
                <FileText size={12} />
                <div>
                  {source?.title || link.source_title || '关联文档'}
                  <small>引用了当前文档</small>
                </div>
              </Link>
            );
          })
        ) : (
          <p className="rail-hint">
            还没有文档引用这里。
            <br />
            让团队用 [[{page.title}]] 建立连接。
          </p>
        )}
        {links.error && <p className="rail-hint">关联信息暂时无法加载。</p>}
        {outgoing.length > 0 && (
          <>
            <div className="rail-divider" />
            <h2 className="rail-label">
              <span>本文链接</span>
              <span className="badge neutral">{outgoing.length}</span>
            </h2>
            {outgoing.map((link) =>
              link.target_id && link.state === 'resolved' ? (
                <Link
                  key={link.id}
                  className="backlink"
                  to={`/w/${workspace.id}/p/${link.target_id}`}
                >
                  <Link2 size={11} />
                  {link.target_title || link.raw_target}
                </Link>
              ) : (
                <div key={link.id} className="rail-hint" style={{ marginBottom: 7 }}>
                  {link.raw_target}{' '}
                  <span className="badge warning">{stateLabels[link.state]}</span>
                </div>
              ),
            )}
          </>
        )}
        <div className="rail-divider" />
        <h2 className="rail-label">
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <Tag size={11} />
            文档标签
          </span>
          {writable && (
            <button
              className="icon-btn"
              style={{ width: 18, height: 18 }}
              onClick={() => onManage(page)}
              aria-label="编辑文档标签"
            >
              <PencilLine size={10} />
            </button>
          )}
        </h2>
        <div className="tag-list">
          {page.tags.length ? (
            page.tags.map((tag) => (
              <span className="tag" key={tag}>
                # {tag}
              </span>
            ))
          ) : (
            <span className="rail-hint">暂时没有标签</span>
          )}
        </div>
        <div className="rail-divider" />
        <h2 className="rail-label">文档信息</h2>
        <dl className="rail-info">
          <dt>创建时间</dt>
          <dd>{date(page.created_at)}</dd>
          <dt>最近更新</dt>
          <dd>{date(page.updated_at)}</dd>
          <dt>当前版本</dt>
          <dd>v{page.version}</dd>
          <dt>内容长度</dt>
          <dd>{chars.toLocaleString()} 字符</dd>
        </dl>
        <div className="rail-tip">
          <strong>
            <Sparkles size={11} />
            知识连接小贴士
          </strong>
          试着用 <code>[[文档标题]]</code> 引用相关内容。连接越多，团队的知识就越有力量。
        </div>
      </aside>
      {history && (
        <Versions
          page={page}
          writable={writable}
          onClose={() => setHistory(false)}
          onRestore={(restored) => {
            accepted(restored);
            toast('历史版本已恢复为新版本');
          }}
        />
      )}
      {remove && (
        <Modal title="将文档移入回收站？" onClose={() => setRemove(false)}>
          <p className="modal-description">
            “{page.title}”将从知识库中移除。你可以在回收站中恢复它。
            {dirty && ' 当前未保存的草稿也将清除。'}
          </p>
          <ErrorNotice error={deletion.error} />
          <div className="modal-actions">
            <button className="button" onClick={() => setRemove(false)}>
              保留文档
            </button>
            <button
              className="button danger"
              onClick={() => deletion.mutate()}
              disabled={deletion.isPending}
            >
              <Trash2 size={13} />
              {deletion.isPending ? '正在移除…' : '移入回收站'}
            </button>
          </div>
        </Modal>
      )}
      {conflict && (
        <Modal title="文档有了新的修改" onClose={() => setConflict(null)} wide>
          <p className="modal-description">
            另一位成员已保存版本 v{conflict.version}
            。你的草稿完整保留在左侧。比较两份内容后，载入服务器版本，或保留草稿继续合并；再次保存将基于
            v{conflict.version} 创建新版本。
          </p>
          <div className="compare-grid">
            <div className="compare-column">
              <h3>你的草稿 · 基于 v{draft.version}</h3>
              <pre>
                {draft.title}
                {'\n\n'}
                {draft.content}
              </pre>
            </div>
            <div className="compare-column">
              <h3>服务器文档 · v{conflict.version}</h3>
              <pre>
                {conflict.title}
                {'\n\n'}
                {conflict.content}
              </pre>
            </div>
          </div>
          <div className="modal-actions">
            <button className="button" onClick={serverCopy}>
              <RefreshCw size={13} />
              载入服务器版本
            </button>
            <button className="button primary" onClick={keepDraft}>
              <PencilLine size={13} />
              保留草稿，继续合并
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function ChildPages({ wid, pid }: { wid: string; pid: string }) {
  const query = usePageTree(wid, pid);
  const children = query.data?.pages.flatMap((batch) => batch.items) || [];
  if (query.isPending) return <Loading text="正在加载子文档…" />;
  if (!children.length && !query.error) return null;
  return (
    <section style={{ borderTop: '1px solid #e4eadd', marginTop: 35, paddingTop: 23 }}>
      <div className="section-heading">
        子文档<span>已加载 {children.length} 篇</span>
      </div>
      <ErrorNotice error={query.error} />
      {query.error && (
        <button className="button small" onClick={() => query.refetch()}>
          重新加载子文档
        </button>
      )}
      {children.map((item) => (
        <Link key={item.id} className="backlink" to={`/w/${wid}/p/${item.id}`}>
          <FileText size={14} />
          <span style={{ flex: 1 }}>{item.title}</span>
          <ArrowUpRight size={12} />
        </Link>
      ))}
      {query.hasNextPage && (
        <button
          className="button small"
          disabled={query.isFetchingNextPage}
          onClick={() => query.fetchNextPage()}
        >
          加载更多子文档
        </button>
      )}
    </section>
  );
}
