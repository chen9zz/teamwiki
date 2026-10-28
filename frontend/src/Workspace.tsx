import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowDownToLine,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsUpDown,
  CircleHelp,
  FilePlus2,
  FileText,
  FolderOpen,
  GitBranch,
  Home,
  Layers3,
  LogOut,
  Menu,
  MoreHorizontal,
  Network,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Users,
  X,
} from 'lucide-react';
import { api, download, message } from './api';
import { availableParents, canManage, canWrite, date, roleLabels } from './lib';
import type { Page, PageSummary, User, Workspace } from './types';
import { Empty, ErrorNotice, Loading, Modal, useToast } from './ui';
import { PAGE_BATCH_SIZE, usePageSummaries, usePageTree } from './pagination';
import { Brand, NewWorkspace } from './App';
import { ImportDialog, MembersView, TrashView, AuditView } from './panels';

const GraphView = lazy(() => import('./GraphView'));
const PageEditor = lazy(() =>
  import('./PageEditor').then((module) => ({ default: module.PageEditor })),
);

interface TreeProps {
  current?: string;
  wid: string;
  writable: boolean;
  onManage: (page: PageSummary) => void;
}
function TreeNode({
  page,
  depth,
  ...props
}: TreeProps & { page: PageSummary; depth: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div
        className={`tree-row ${props.current === page.id ? 'selected' : ''}`}
        style={{ paddingLeft: 9 + depth * 13 }}
      >
        <button
          className="tree-toggle"
          disabled={!page.has_children}
          aria-label={`${open ? '收起' : '展开'} ${page.title}`}
          aria-expanded={page.has_children ? open : undefined}
          onClick={() => setOpen(!open)}
        >
          {page.has_children ? (
            open ? (
              <ChevronDown size={12} />
            ) : (
              <ChevronRight size={12} />
            )
          ) : (
            <span style={{ width: 12 }} />
          )}
        </button>
        <Link className="tree-link" to={`/w/${props.wid}/p/${page.id}`} title={page.title}>
          <FileText size={13} />
          <span>{page.title}</span>
        </Link>
        {props.writable && (
          <button
            className="icon-btn"
            onClick={() => props.onManage(page)}
            aria-label={`管理 ${page.title}`}
          >
            <MoreHorizontal size={13} />
          </button>
        )}
      </div>
      {open && page.has_children && <TreeBranch {...props} parent={page} depth={depth + 1} />}
    </div>
  );
}
function TreeBranch({
  parent,
  depth = 0,
  ...props
}: TreeProps & { parent?: PageSummary; depth?: number }) {
  const query = usePageTree(props.wid, parent?.id);
  const pages = query.data?.pages.flatMap((batch) => batch.items) || [];
  return (
    <>
      {query.isPending && <Loading />}
      <ErrorNotice error={query.error} />
      {query.error && (
        <button className="button small" onClick={() => query.refetch()}>
          重新加载目录
        </button>
      )}
      {pages.map((page) => (
        <TreeNode key={page.id} {...props} page={page} depth={depth} />
      ))}
      {query.hasNextPage && (
        <button
          className="button small"
          style={{ margin: '8px 12px' }}
          aria-label={parent ? `加载更多 ${parent.title} 的子文档` : '加载更多根文档'}
          disabled={query.isFetchingNextPage}
          onClick={() => query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? '正在加载…' : '加载更多'}
        </button>
      )}
      {!query.isPending && !query.error && !pages.length && !parent && (
        <div style={{ padding: '14px 19px', fontSize: 10, color: '#779484' }}>
          还没有文档。
        </div>
      )}
    </>
  );
}

export function PageDialog({
  wid,
  pages,
  page,
  parent,
  onClose,
  onSaved,
}: {
  wid: string;
  pages: PageSummary[];
  page?: Page;
  parent?: string;
  onClose: () => void;
  onSaved: (page: Page) => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const summaries = usePageSummaries(wid);
  const [selectedParent, setSelectedParent] = useState(page?.parent_id || parent || '');
  const candidates = [
    ...new Map(
      [...pages, ...(summaries.data?.pages.flatMap((batch) => batch.items) || [])].map(
        (item) => [item.id, item],
      ),
    ).values(),
  ];
  const parents = availableParents(candidates, page?.id);
  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      page
        ? api<Page>(`/workspaces/${wid}/pages/${page.id}`, {
            method: 'PATCH',
            body: { ...body, version: page.version },
          })
        : api<Page>(`/workspaces/${wid}/pages`, {
            method: 'POST',
            body: { ...body, content: '' },
          }),
    onSuccess: (saved) => {
      client.setQueryData(['page', saved.id], saved);
      client.invalidateQueries({ queryKey: ['pages', wid] });
      client.invalidateQueries({ queryKey: ['graph', wid] });
      onSaved(saved);
      toast(page ? '文档信息已更新' : '新文档已创建');
      onClose();
    },
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    mutation.mutate({
      title: String(form.get('title')).trim(),
      parent_id: form.get('parent_id') || null,
      tags: String(form.get('tags'))
        .split(/[,，]/)
        .map((tag) => tag.trim())
        .filter(Boolean),
    });
  }
  return (
    <Modal title={page ? '文档设置' : '创建新文档'} onClose={onClose}>
      <p className="modal-description">
        {page
          ? '调整标题、所属位置和标签。移动文档时，所有子页面会跟随文档一起移动。'
          : '好的知识，从一次记录开始。你可以随时修改标题、添加内容或移动位置。'}
      </p>
      <form onSubmit={submit}>
        <label className="field">
          <span>文档标题</span>
          <input
            name="title"
            required
            maxLength={240}
            defaultValue={page?.title}
            placeholder="给这篇文档一个清晰的名字"
          />
        </label>
        <label className="field">
          <span>上级文档</span>
          <select
            name="parent_id"
            value={selectedParent}
            onChange={(event) => setSelectedParent(event.target.value)}
          >
            <option value="">空间根目录</option>
            {selectedParent && !parents.some((item) => item.id === selectedParent) && (
              <option value={selectedParent}>当前上级（未加载）</option>
            )}
            {parents.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
          <small>候选文档按标题分批加载；当前上级会保留。</small>
          {summaries.hasNextPage && (
            <button
              type="button"
              className="button small"
              disabled={summaries.isFetchingNextPage}
              onClick={() => summaries.fetchNextPage()}
            >
              加载更多上级文档
            </button>
          )}
          <ErrorNotice error={summaries.error} />
        </label>
        <label className="field">
          <span>标签</span>
          <input
            name="tags"
            defaultValue={page?.tags?.join(', ')}
            placeholder="团队规范, 入门指南"
          />
          <small>用逗号分隔，帮助团队更快找到相关知识。</small>
        </label>
        <ErrorNotice error={mutation.error} />
        <div className="modal-actions">
          <button className="button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={mutation.isPending}>
            <Check size={13} />
            {mutation.isPending ? '正在保存…' : page ? '保存设置' : '创建文档'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
function SearchDialog({ wid, onClose }: { wid: string; onClose: () => void }) {
  const [input, setInput] = useState('');
  const [q, setQ] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setQ(input.trim()), 250);
    return () => clearTimeout(timer);
  }, [input]);
  const query = useInfiniteQuery({
    queryKey: ['search', wid, q],
    initialPageParam: 0,
    getNextPageParam: (last: (Page & { rank: number })[], batches) =>
      last.length === PAGE_BATCH_SIZE ? batches.length * PAGE_BATCH_SIZE : undefined,
    queryFn: ({ signal, pageParam }) =>
      api<(Page & { rank: number })[]>(
        `/workspaces/${wid}/search?q=${encodeURIComponent(q)}&limit=${PAGE_BATCH_SIZE}&offset=${pageParam}`,
        {
          signal,
        },
      ),
    enabled: !!q,
  });
  return (
    <Modal title="搜索团队知识" onClose={onClose}>
      <div className="search-input">
        <Search size={19} color="#8da17b" />
        <input
          aria-label="搜索文档"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入标题、内容或关键词…"
        />
      </div>
      {query.isFetching ? (
        <Loading text="搜索中…" />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : q && query.data?.pages.some((batch) => batch.length) ? (
        query.data.pages.flat().map((page) => (
          <Link
            key={page.id}
            className="search-result"
            to={`/w/${wid}/p/${page.id}`}
            onClick={onClose}
          >
            <strong>
              <FileText size={15} />
              {page.title}
            </strong>
            <p>{page.content.replace(/[#*`>]/g, '').slice(0, 150) || '此文档暂时没有正文'}</p>
          </Link>
        ))
      ) : (
        <div className="search-hint">
          {q ? '没有匹配的文档，试试其他关键词。' : '搜索标题与正文，让答案更近一点。'}
        </div>
      )}
      {query.hasNextPage && (
        <button
          className="button"
          disabled={query.isFetchingNextPage}
          onClick={() => query.fetchNextPage()}
        >
          加载更多搜索结果
        </button>
      )}
      <div className="search-hint" style={{ paddingBottom: 0, fontSize: 9 }}>
        按 ESC 关闭 &nbsp;·&nbsp; 空间内的搜索结果遵循你的访问权限
      </div>
    </Modal>
  );
}
function Dashboard({
  user,
  workspace,
  pages,
  writable,
  onCreate,
}: {
  user: User;
  workspace: Workspace;
  pages: PageSummary[];
  writable: boolean;
  onCreate: () => void;
}) {
  const recent = [...pages].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const tags = new Set(pages.flatMap((page) => page.tags));
  return (
    <div className="dashboard">
      <div className="dashboard-title-row">
        <div>
          <div className="section-eyebrow">A LITTLE KNOWLEDGE, EVERY DAY</div>
          <h1>
            你好，{user.name} <span style={{ fontSize: 22 }}>☀</span>
          </h1>
          <div style={{ fontSize: 11, color: '#97a58a' }}>
            欢迎回到 {workspace.name}，接着探索和创造。
          </div>
        </div>
        {writable && (
          <button className="button primary" onClick={onCreate}>
            <Plus size={14} />
            新建文档
          </button>
        )}
      </div>
      <div className="welcome-banner">
        <h2>让每一份经验，都有迹可循。</h2>
        <p>
          写下想法，连接知识。在文档中输入 [[文档标题]]，把零散的信息织成团队共同的知识网络。
        </p>
      </div>
      <div className="stat-row">
        <div className="stat">
          <span>
            <FileText size={20} />
          </span>
          <div>
            <strong>{pages.length}</strong>
            <small>已加载文档</small>
          </div>
        </div>
        <div className="stat">
          <span>
            <Layers3 size={20} />
          </span>
          <div>
            <strong>{pages.filter((page) => !page.parent_id).length}</strong>
            <small>已加载根文档</small>
          </div>
        </div>
        <div className="stat">
          <span>
            <GitBranch size={20} />
          </span>
          <div>
            <strong>{tags.size}</strong>
            <small>已加载标签</small>
          </div>
        </div>
      </div>
      <div className="section-heading">
        已加载文档<span>按标题分批加载，数量仅代表当前已加载部分</span>
      </div>
      {recent.length ? (
        <div className="page-cards">
          {recent.map((page) => (
            <Link className="page-card" key={page.id} to={`/w/${workspace.id}/p/${page.id}`}>
              <div className="card-icon">
                <FileText size={17} />
              </div>
              <h3>{page.title}</h3>
              <p>{page.tags.length ? page.tags.join(' · ') : '打开文档，阅读团队知识。'}</p>
              <footer>
                <span>{date(page.updated_at)}</span>
                <span>v{page.version} &nbsp;↗</span>
              </footer>
            </Link>
          ))}
        </div>
      ) : (
        <Empty
          title="知识，从这里生长"
          description="创建第一篇文档，邀请同事一起把经验变成团队的共同财富。"
        >
          {writable && (
            <button className="button" onClick={onCreate}>
              <FilePlus2 size={14} />
              写下第一篇
            </button>
          )}
        </Empty>
      )}
    </div>
  );
}
export function WorkspaceApp(props: { user: User; workspaces: Workspace[] }) {
  const location = useLocation();
  const route = location.pathname.split('/').filter(Boolean);
  const wid = route[0] === 'w' ? route[1] : '';
  const workspace = props.workspaces.find((item) => item.id === wid) || props.workspaces[0];
  // Workspace-scoped dialogs and pending requests must not survive a space switch.
  return <WorkspaceReady key={workspace.id} {...props} />;
}
function WorkspaceReady({ user, workspaces }: { user: User; workspaces: Workspace[] }) {
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const toast = useToast();
  const route = location.pathname.split('/').filter(Boolean);
  const wid = route[0] === 'w' ? route[1] : '';
  const workspace = workspaces.find((item) => item.id === wid) || workspaces[0];
  const view = route[2] || 'home';
  const pid = view === 'p' ? route[3] : undefined;
  const [mobile, setMobile] = useState(false);
  const [search, setSearch] = useState(false);
  const [newSpace, setNewSpace] = useState(false);
  const [importing, setImporting] = useState(false);
  const [documentDialog, setDocumentDialog] = useState<{
    page?: Page;
    parent?: string;
  } | null>(null);
  const managementRequest = useRef<AbortController | null>(null);
  function cancelManagement() {
    managementRequest.current?.abort();
    managementRequest.current = null;
  }
  // Every dialog intent supersedes pending detail loads, including create and close.
  function changeDocumentDialog(next: typeof documentDialog) {
    cancelManagement();
    setDocumentDialog(next);
  }
  useEffect(() => () => cancelManagement(), []);
  async function managePage(summary: PageSummary) {
    cancelManagement();
    const request = new AbortController();
    managementRequest.current = request;
    try {
      const page = await api<Page>(`/workspaces/${workspace.id}/pages/${summary.id}`, {
        signal: request.signal,
      });
      if (!request.signal.aborted && managementRequest.current === request) {
        changeDocumentDialog({ page });
      }
    } catch (error) {
      if (!request.signal.aborted && managementRequest.current === request) {
        toast(message(error), true);
      }
    } finally {
      if (managementRequest.current === request) managementRequest.current = null;
    }
  }
  const writable = canWrite(workspace.role);
  const manageable = canManage(workspace.role);
  const pages = usePageSummaries(workspace.id, !pid && view === 'home');
  const logout = useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      client.clear();
      navigate('/');
      window.location.reload();
    },
    onError: (error) => toast(message(error), true),
  });
  useEffect(() => {
    if (!wid || !workspaces.some((item) => item.id === wid))
      navigate(`/w/${workspace.id}`, { replace: true });
  }, [wid, workspace.id, workspaces, navigate]);
  useEffect(() => {
    setMobile(false);
  }, [location.pathname]);
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearch((value) => !value);
      }
    }
    document.addEventListener('keydown', shortcut);
    return () => document.removeEventListener('keydown', shortcut);
  }, []);
  const list = pages.data?.pages.flatMap((batch) => batch.items) || [];
  const selected = list.find((page) => page.id === pid);
  const titles: Record<string, string> = {
    home: '空间概览',
    graph: '知识图谱',
    trash: '回收站',
    members: '成员管理',
    audit: '活动记录',
  };
  async function exportSpace() {
    try {
      await download(`/workspaces/${workspace.id}/export`, `${workspace.name}.zip`);
      toast('空间文档已导出');
    } catch (error) {
      toast(message(error), true);
    }
  }
  function openPage(page: Page) {
    navigate(`/w/${workspace.id}/p/${page.id}`);
  }
  const navItems = [
    { key: 'home', label: '空间概览', icon: Home },
    { key: 'graph', label: '知识图谱', icon: Network },
    { key: 'members', label: '团队成员', icon: Users },
  ];
  return (
    <div className="app">
      {mobile && (
        <button
          className="sidebar-backdrop"
          aria-label="关闭侧栏"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={`sidebar ${mobile ? 'open' : ''}`}>
        <Brand />
        <div className="workspace-switch">
          <div className="workspace-avatar">{workspace.name.slice(0, 1)}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <select
              aria-label="选择知识空间"
              value={workspace.id}
              onChange={(event) =>
                event.target.value === '__new'
                  ? setNewSpace(true)
                  : navigate(`/w/${event.target.value}`)
              }
            >
              {workspaces.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
              <option value="__new">＋ 创建新空间</option>
            </select>
            <div className="workspace-subtitle">TEAM WORKSPACE</div>
          </div>
        </div>
        <button className="nav-search" onClick={() => setSearch(true)}>
          <Search size={14} />
          搜索知识库<kbd>⌘ K</kbd>
        </button>
        <nav>
          {navItems.map((item) => (
            <Link
              className={`nav-item ${view === item.key ? 'active' : ''}`}
              key={item.key}
              to={`/w/${workspace.id}${item.key === 'home' ? '' : `/${item.key}`}`}
            >
              <item.icon size={15} strokeWidth={1.7} />
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-rule" />
        <div className="tree-heading">
          <span>知识库文档</span>
          {writable && (
            <button
              className="icon-btn"
              onClick={() => changeDocumentDialog({})}
              aria-label="新建文档"
            >
              <Plus size={15} />
            </button>
          )}
        </div>
        <div className="page-tree">
          <TreeBranch
            key={workspace.id}
            current={pid}
            wid={workspace.id}
            writable={writable}
            onManage={managePage}
          />
        </div>
        <div className="sidebar-bottom">
          {manageable && (
            <button className="nav-item" onClick={() => setImporting(true)}>
              <Upload size={14} />
              导入 Markdown<span className="count">.md / .zip</span>
            </button>
          )}
          <button className="nav-item" onClick={exportSpace}>
            <ArrowDownToLine size={14} />
            导出知识库
          </button>
          <Link
            className={`nav-item ${view === 'trash' ? 'active' : ''}`}
            to={`/w/${workspace.id}/trash`}
          >
            <Trash2 size={14} />
            回收站
          </Link>
          {manageable && (
            <Link
              className={`nav-item ${view === 'audit' ? 'active' : ''}`}
              to={`/w/${workspace.id}/audit`}
            >
              <ShieldCheck size={14} />
              活动记录
            </Link>
          )}
          <div className="sidebar-note">
            <strong>
              <Sparkles size={11} />
              让知识，在连接中生长
            </strong>
            <p>
              用 [[双向链接]] 连接彼此的想法。
              <br />
              属于团队的第二大脑。
            </p>
          </div>
          <div className="account">
            <div className="avatar">{user.name.slice(0, 1).toUpperCase()}</div>
            <div style={{ minWidth: 0 }}>
              <div className="account-name">{user.name}</div>
              <div className="account-role">{roleLabels[workspace.role]} · 个人账户</div>
            </div>
            <button
              className="icon-btn"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              aria-label="退出登录"
              title="退出登录"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <button
            className="icon-btn mobile-menu"
            onClick={() => setMobile(true)}
            aria-label="打开导航"
          >
            <Menu size={18} />
          </button>
          <div className="breadcrumb">
            <BookOpen size={13} />
            <Link to={`/w/${workspace.id}`}>{workspace.name}</Link>
            <ChevronRight size={11} />
            <span>{selected?.title || titles[view] || '文档'}</span>
          </div>
          <div className="topbar-right">
            <div className="presence">
              <i />
              团队知识空间
            </div>
            <span className="badge neutral">{roleLabels[workspace.role]}</span>
            {writable && (
              <button
                className="button small"
                onClick={() => changeDocumentDialog({ parent: pid })}
              >
                <Plus size={12} />
                新建文档
              </button>
            )}
            <button className="icon-btn" onClick={() => setSearch(true)} aria-label="搜索">
              <Search size={16} />
            </button>
          </div>
        </header>
        <div className="main-body">
          {!pid && view === 'home' && pages.error ? (
            <Empty title="文档加载失败" description="空间暂时无法访问，请稍后重试。">
              <ErrorNotice error={pages.error} />
              <button className="button" onClick={() => pages.refetch()}>
                重新加载
              </button>
            </Empty>
          ) : pid ? (
            <Suspense fallback={<Loading text="正在打开文档…" />}>
              <PageEditor
                key={pid}
                pid={pid}
                workspace={workspace}
                pages={list}
                user={user}
                onManage={(page) => changeDocumentDialog({ page })}
                onCreateChild={(page) => changeDocumentDialog({ parent: page.id })}
              />
            </Suspense>
          ) : view === 'graph' ? (
            <Suspense fallback={<Loading text="正在绘制知识网络…" />}>
              <GraphView wid={workspace.id} />
            </Suspense>
          ) : view === 'members' ? (
            <MembersView workspace={workspace} user={user} />
          ) : view === 'trash' ? (
            <TrashView workspace={workspace} />
          ) : view === 'audit' ? (
            manageable ? (
              <AuditView wid={workspace.id} />
            ) : (
              <Empty
                title="需要管理员权限"
                description="只有空间管理员和所有者可以查看活动记录。"
              />
            )
          ) : pages.isPending ? (
            <Loading />
          ) : (
            <>
              <Dashboard
                user={user}
                workspace={workspace}
                pages={list}
                writable={writable}
                onCreate={() => changeDocumentDialog({})}
              />
              {pages.hasNextPage && (
                <button
                  className="button"
                  style={{ margin: '0 32px 24px' }}
                  disabled={pages.isFetchingNextPage}
                  onClick={() => pages.fetchNextPage()}
                >
                  加载更多文档
                </button>
              )}
            </>
          )}
        </div>
      </main>
      {search && (
        <SearchDialog
          key={workspace.id}
          wid={workspace.id}
          onClose={() => setSearch(false)}
        />
      )}{' '}
      {newSpace && <NewWorkspace onClose={() => setNewSpace(false)} />}{' '}
      {importing && <ImportDialog wid={workspace.id} onClose={() => setImporting(false)} />}{' '}
      {documentDialog && (
        <PageDialog
          key={`${workspace.id}:${documentDialog.page?.id || 'new'}:${documentDialog.parent || ''}`}
          wid={workspace.id}
          pages={list}
          page={documentDialog.page}
          parent={documentDialog.parent}
          onClose={() => changeDocumentDialog(null)}
          onSaved={openPage}
        />
      )}
    </div>
  );
}
