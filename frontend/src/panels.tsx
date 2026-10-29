import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  CheckCircle2,
  Copy,
  FileArchive,
  FileText,
  GitBranch,
  LoaderCircle,
  MailPlus,
  Network,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
  Upload,
  UserPlus,
  Users,
  X,
} from 'lucide-react';
import { api, download, message } from './api';
import { canManage, canWrite, date, roleLabels } from './lib';
import type {
  AuditEvent,
  GraphData,
  ImportJob,
  Member,
  Page,
  Role,
  User,
  Workspace,
} from './types';
import { Empty, ErrorNotice, Loading, Modal, useToast } from './ui';

function InviteDialog({ workspace, onClose }: { workspace: Workspace; onClose: () => void }) {
  const [result, setResult] = useState<{
    token: string;
    email: string;
    role: Role;
    expires_at: string;
  } | null>(null);
  const toast = useToast();
  const mutation = useMutation({
    mutationFn: (body: { email: string; role: string }) =>
      api<{ token: string; email: string; role: Role; expires_at: string }>(
        `/workspaces/${workspace.id}/invitations`,
        { method: 'POST', body },
      ),
    onSuccess: setResult,
  });
  const link = result
    ? `${window.location.origin}/invite?token=${encodeURIComponent(result.token)}`
    : '';
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    mutation.mutate({ email: String(form.get('email')), role: String(form.get('role')) });
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
      toast('邀请链接已复制');
    } catch {
      toast('请选中并复制邀请链接。', true);
    }
  }
  return (
    <Modal title={result ? '邀请已创建' : '邀请团队成员'} onClose={onClose}>
      {result ? (
        <>
          <p className="modal-description">
            将下面的链接分享给 <strong>{result.email}</strong>
            。对方需要使用该邮箱注册或登录，才能以“{roleLabels[result.role]}”身份加入空间。
          </p>
          <div className="invite-token">{link}</div>
          <p className="rail-hint" style={{ marginTop: 12 }}>
            有效期至 {date(result.expires_at, true)} · 邀请仅可使用一次
          </p>
          <div className="modal-actions">
            <button className="button" onClick={onClose}>
              完成
            </button>
            <button className="button primary" onClick={copy}>
              <Copy size={13} />
              复制邀请链接
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="modal-description">
            邀请同事一起积累知识。邀请会生成一个专属链接，由你分享给对方。
          </p>
          <form onSubmit={submit}>
            <label className="field">
              <span>成员邮箱</span>
              <input name="email" type="email" required placeholder="colleague@company.com" />
            </label>
            <label className="field">
              <span>访问角色</span>
              <select name="role" defaultValue="editor">
                <option value="viewer">阅读者 · 可以阅读与导出文档</option>
                <option value="editor">编辑者 · 可以创建与编辑文档</option>
                {workspace.role === 'owner' && (
                  <option value="admin">管理员 · 管理成员与导入文档</option>
                )}
              </select>
            </label>
            <ErrorNotice error={mutation.error} />
            <div className="modal-actions">
              <button className="button" type="button" onClick={onClose}>
                取消
              </button>
              <button className="button primary" disabled={mutation.isPending}>
                <MailPlus size={13} />
                {mutation.isPending ? '正在创建…' : '生成邀请链接'}
              </button>
            </div>
          </form>
        </>
      )}
    </Modal>
  );
}
export function MembersView({ workspace, user }: { workspace: Workspace; user: User }) {
  const [invite, setInvite] = useState(false);
  const [remove, setRemove] = useState<Member | null>(null);
  const client = useQueryClient();
  const toast = useToast();
  const manageable = canManage(workspace.role);
  const query = useQuery({
    queryKey: ['members', workspace.id],
    queryFn: () => api<Member[]>(`/workspaces/${workspace.id}/members`),
  });
  const change = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) =>
      api(`/workspaces/${workspace.id}/members/${id}`, { method: 'PATCH', body: { role } }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['members', workspace.id] });
      client.invalidateQueries({ queryKey: ['workspaces'] });
      toast('成员角色已更新');
    },
    onError: (error) => toast(message(error), true),
  });
  const deletion = useMutation({
    mutationFn: (id: string) =>
      api(`/workspaces/${workspace.id}/members/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['members', workspace.id] });
      toast('成员已从空间移除');
      setRemove(null);
    },
  });
  function canEdit(member: Member) {
    return (
      manageable &&
      member.role !== 'owner' &&
      (workspace.role === 'owner' || member.role !== 'admin') &&
      member.user_id !== user.id
    );
  }
  return (
    <div className="panel-view">
      <div className="panel-header">
        <div>
          <div className="section-eyebrow">BETTER TOGETHER</div>
          <h1>团队成员</h1>
          <p>分享知识，也共享成长。{query.data?.length || 0} 位成员在这里协作。</p>
        </div>
        {manageable && (
          <button className="button primary" onClick={() => setInvite(true)}>
            <UserPlus size={14} />
            邀请成员
          </button>
        )}
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>成员</th>
              <th>角色与权限</th>
              <th style={{ textAlign: 'right' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {query.data?.map((member) => (
              <tr key={member.user_id}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                    <span
                      style={{
                        display: 'grid',
                        placeItems: 'center',
                        width: 32,
                        height: 32,
                        background: '#e9efdf',
                        borderRadius: '50%',
                        color: '#7c9862',
                        fontSize: 12,
                      }}
                    >
                      {member.name.slice(0, 1)}
                    </span>
                    <div>
                      {member.name}{' '}
                      {member.user_id === user.id && <span className="badge neutral">你</span>}
                      <div className="sub">{member.email}</div>
                    </div>
                  </div>
                </td>
                <td>
                  {canEdit(member) ? (
                    <select
                      value={member.role}
                      disabled={change.isPending}
                      aria-label={`${member.name}的角色`}
                      onChange={(event) =>
                        change.mutate({ id: member.user_id, role: event.target.value as Role })
                      }
                    >
                      <option value="viewer">阅读者</option>
                      <option value="editor">编辑者</option>
                      {workspace.role === 'owner' && <option value="admin">管理员</option>}
                    </select>
                  ) : (
                    <span className={`badge ${member.role === 'owner' ? '' : 'neutral'}`}>
                      {roleLabels[member.role]}
                    </span>
                  )}
                </td>
                <td style={{ textAlign: 'right' }}>
                  {canEdit(member) ? (
                    <button
                      className="icon-btn"
                      onClick={() => setRemove(member)}
                      aria-label={`移除成员 ${member.name}`}
                    >
                      <Trash2 size={14} />
                    </button>
                  ) : (
                    <span className="muted" style={{ fontSize: 10 }}>
                      —
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div
        style={{
          marginTop: 30,
          padding: 18,
          border: '1px solid #e3e9d9',
          borderRadius: 7,
          color: '#92a47e',
          fontSize: 10,
          lineHeight: 2,
        }}
      >
        <ShieldCheck
          size={13}
          style={{ display: 'inline', verticalAlign: 'middle', marginRight: 6 }}
        />
        所有者拥有空间的完整控制权。管理员可以邀请和管理编辑者、阅读者。编辑者可以维护文档；阅读者可以查看和导出知识。
      </div>
      {invite && <InviteDialog workspace={workspace} onClose={() => setInvite(false)} />}{' '}
      {remove && (
        <Modal title="移除团队成员？" onClose={() => setRemove(null)}>
          <p className="modal-description">
            移除后，{remove.name}（{remove.email}）将无法继续访问此空间。已创建的文档会保留。
          </p>
          <ErrorNotice error={deletion.error} />
          <div className="modal-actions">
            <button className="button" onClick={() => setRemove(null)}>
              取消
            </button>
            <button
              className="button danger"
              disabled={deletion.isPending}
              onClick={() => deletion.mutate(remove.user_id)}
            >
              确认移除
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
export function TrashView({ workspace }: { workspace: Workspace }) {
  const client = useQueryClient();
  const toast = useToast();
  const query = useQuery({
    queryKey: ['trash', workspace.id],
    queryFn: () => api<Page[]>(`/workspaces/${workspace.id}/pages?deleted=true`),
  });
  const restore = useMutation({
    mutationFn: (page: Page) =>
      api<Page>(`/workspaces/${workspace.id}/pages/${page.id}/undelete`, {
        method: 'POST',
        body: { version: page.version },
      }),
    onSuccess: (page) => {
      client.setQueryData(['page', page.id], page);
      client.invalidateQueries({ queryKey: ['trash', workspace.id] });
      client.invalidateQueries({ queryKey: ['pages', workspace.id] });
      client.invalidateQueries({ queryKey: ['graph', workspace.id] });
      toast('文档已恢复到知识库');
    },
    onError: (error) => {
      toast(message(error), true);
      client.invalidateQueries({ queryKey: ['trash', workspace.id] });
    },
  });
  return (
    <div className="panel-view">
      <div className="panel-header">
        <div>
          <div className="section-eyebrow">NOTHING VALUABLE IS LOST</div>
          <h1>回收站</h1>
          <p>暂时放下的知识，也值得妥善保存。</p>
        </div>
        <button className="button" onClick={() => query.refetch()}>
          <RefreshCw size={13} />
          刷新
        </button>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.length ? (
        <table className="table">
          <thead>
            <tr>
              <th>文档</th>
              <th>删除时间</th>
              <th style={{ textAlign: 'right' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {query.data.map((page) => (
              <tr key={page.id}>
                <td>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <FileText size={14} />
                    {page.title}
                  </div>
                  <div className="sub">版本 {page.version}</div>
                </td>
                <td>{date(page.deleted_at || undefined, true)}</td>
                <td style={{ textAlign: 'right' }}>
                  {canWrite(workspace.role) ? (
                    <button
                      className="button small"
                      disabled={restore.isPending}
                      onClick={() => restore.mutate(page)}
                    >
                      <RotateCcw size={12} />
                      恢复文档
                    </button>
                  ) : (
                    <span className="muted">需要编辑权限</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty
          title="回收站是空的"
          description="删除的文档会出现在这里。有编辑权限的成员可以随时恢复文档和它们的知识连接。"
        />
      )}
    </div>
  );
}
export function ExportDialog({ workspace, onClose }: { workspace: Workspace; onClose: () => void }) {
  const [format, setFormat] = useState<'teamwiki' | 'markdown'>('teamwiki');
  const toast = useToast();
  const exportArchive = useMutation({
    mutationFn: () => download(
      `/workspaces/${workspace.id}/export${format === 'teamwiki' ? '?format=teamwiki' : ''}`,
      `${workspace.name}.${format}.zip`,
    ),
    onSuccess: () => {
      toast('空间文档已导出');
      onClose();
    },
  });
  return (
    <Modal title="导出团队知识" onClose={onClose}>
      <p className="modal-description">
        导出当前有效文档。选择 TeamWiki 完整格式，可以在另一个空间恢复标题、目录层级、标签和归档内的链接关系。
      </p>
      <label className="field">
        <span>导出格式</span>
        <select
          aria-label="导出格式"
          value={format}
          disabled={exportArchive.isPending}
          onChange={(event) => {
            setFormat(event.target.value as 'teamwiki' | 'markdown');
            exportArchive.reset();
          }}
        >
          <option value="teamwiki">TeamWiki 完整格式（ZIP）</option>
          <option value="markdown">纯 Markdown（ZIP）</option>
        </select>
        <small>
          {format === 'teamwiki'
            ? '包含文档正文与元数据；再次导入时创建新文档 ID，并重新连接归档内的文档。'
            : '正文按 Markdown 文件平铺导出；不携带目录、标签和链接绑定元数据。'}
        </small>
      </label>
      <p className="rail-hint">
        {format === 'teamwiki'
          ? '最多 400 篇文档、20 层目录；正文合计最多 16 MiB，单篇最多 2 MiB / 100 万字符，下载 ZIP 最多 8 MiB。'
          : '最多 2,000 篇文档，正文合计最多 32 MiB；文件平铺导出，不保留层级或标签。'}
        账号、成员权限、活动记录和历史版本不在导出范围。
      </p>
      <p className="rail-hint">下载在当前请求内生成，请等待文件准备完成。</p>
      <ErrorNotice error={exportArchive.error} />
      <div className="modal-actions">
        <button className="button" onClick={onClose}>取消</button>
        <button className="button primary" disabled={exportArchive.isPending} onClick={() => exportArchive.mutate()}>
          <ArrowDownToLine size={13} />
          {exportArchive.isPending ? '正在生成…' : '下载 ZIP'}
        </button>
      </div>
    </Modal>
  );
}

export function ImportDialog({ wid, onClose }: { wid: string; onClose: () => void }) {
  const storageKey = `teamwiki-import:${wid}`;
  const [file, setFile] = useState<File | null>(null);
  const [idempotency, setIdempotency] = useState(() => crypto.randomUUID());
  const [jobId, setJobId] = useState<string | null>(() => sessionStorage.getItem(storageKey));
  const [localError, setLocalError] = useState<string | null>(null);
  const client = useQueryClient();
  const toast = useToast();
  const notified = useRef('');
  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('请先选择文件');
      const form = new FormData();
      form.append('file', file);
      return api<ImportJob>(`/workspaces/${wid}/imports`, {
        method: 'POST',
        body: form,
        headers: { 'Idempotency-Key': idempotency },
      });
    },
    onSuccess: (job) => {
      setJobId(job.id);
      sessionStorage.setItem(storageKey, job.id);
      client.setQueryData(['import', wid, job.id], job);
    },
  });
  const job = useQuery({
    queryKey: ['import', wid, jobId],
    queryFn: () => api<ImportJob>(`/workspaces/${wid}/imports/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) =>
      ['completed', 'failed'].includes(query.state.data?.status || '') ? false : 1500,
  });
  useEffect(() => {
    if (job.data?.status === 'completed' && notified.current !== job.data.id) {
      notified.current = job.data.id;
      client.invalidateQueries({ queryKey: ['pages', wid] });
      client.invalidateQueries({ queryKey: ['graph', wid] });
      toast(`导入完成，${job.data.processed} 篇文档已加入知识库`);
    }
  }, [job.data, client, wid, toast]);
  const finished = job.data?.status === 'completed';
  const failed = job.data?.status === 'failed';
  const labels: Record<string, string> = {
    queued: '正在排队，等待后台处理',
    running: '正在整理并连接文档',
    completed: '导入已完成',
    failed: '导入失败',
  };
  function choose(selected: File | null) {
    setLocalError(null);
    if (selected && selected.size > 8 * 1024 * 1024) {
      setFile(null);
      upload.reset();
      setLocalError('文件超过 8 MiB，请分批导入。');
      return;
    }
    setFile(selected);
    setIdempotency(crypto.randomUUID());
    upload.reset();
  }
  function reset() {
    setJobId(null);
    sessionStorage.removeItem(storageKey);
    setFile(null);
    setIdempotency(crypto.randomUUID());
    upload.reset();
  }
  return (
    <Modal title="导入团队知识" onClose={onClose}>
      <p className="modal-description">
        支持 UTF-8 Markdown、普通 Markdown ZIP 和 TeamWiki 完整格式 ZIP。上传最多 8 MiB；
        解压后正文合计最多 16 MiB，单篇最多 2 MiB / 100 万字符，目录最多 20 层。
      </p>
      <ul className="rail-hint">
        <li>普通 Markdown ZIP：最多 200 个 Markdown 文件、200 个目录，共 400 篇文档。文件名用作标题，文件夹转成目录文档。</li>
        <li>TeamWiki 完整格式：最多 400 篇文档，元数据最多 1 MiB。恢复标题、目录、标签和归档内的链接关系；创建新 ID，保留目标空间的已有文档。</li>
      </ul>
      {jobId ? (
        <>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              color: finished ? '#71975b' : failed ? '#b27b5d' : '#8fa478',
              fontSize: 13,
            }}
          >
            {finished ? (
              <CheckCircle2 size={22} />
            ) : failed ? (
              <X size={22} />
            ) : (
              <LoaderCircle size={22} className="spinner" />
            )}
            <span>
              {job.data ? labels[job.data.status] || job.data.status : '正在读取导入进度…'}
            </span>
          </div>
          <div className="progress">
            <span
              style={{
                width: `${finished ? 100 : job.data?.total ? Math.max(5, (job.data.processed / job.data.total) * 100) : 8}%`,
              }}
            />
          </div>
          <p className="rail-hint">
            已处理 {job.data?.processed || 0} / {job.data?.total || 0} 篇文档{' '}
            {!finished && !failed && '· 你可以关闭窗口，导入会在后台继续'}
          </p>
          <ErrorNotice error={job.error || job.data?.error} />
          <div className="modal-actions">
            {(finished || failed || job.error) && (
              <button className="button" onClick={reset}>
                导入其他文件
              </button>
            )}
            <button className="button primary" onClick={onClose}>
              {finished ? '返回知识空间' : '关闭窗口'}
            </button>
          </div>
        </>
      ) : (
        <>
          <label className="file-drop">
            <Upload size={27} strokeWidth={1.4} style={{ margin: '0 auto' }} />
            <strong>{file ? file.name : '选择 Markdown、普通 ZIP 或 TeamWiki ZIP'}</strong>
            <small>
              {file ? `${(file.size / 1024).toFixed(1)} KB` : '从文件夹开始，把团队经验整理成知识'}
            </small>
            <input
              type="file"
              accept=".md,.markdown,.zip"
              onChange={(event) => choose(event.target.files?.[0] || null)}
              style={{ display: 'block', fontSize: 10, maxWidth: '100%', margin: '15px auto 0' }}
              aria-label="选择导入文件"
            />
          </label>
          <div style={{ marginTop: 14 }}>
            <ErrorNotice error={localError || upload.error} />
          </div>
          <div className="modal-actions">
            <button className="button" onClick={onClose}>
              取消
            </button>
            <button
              className="button primary"
              disabled={!file || upload.isPending}
              onClick={() => upload.mutate()}
            >
              <Upload size={13} />
              {upload.isPending ? '正在上传…' : '开始导入'}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
export function AuditView({ wid }: { wid: string }) {
  const query = useQuery({
    queryKey: ['audit', wid],
    queryFn: () => api<AuditEvent[]>(`/workspaces/${wid}/audit`),
  });
  const actionLabels: Record<string, string> = {
    'page.create': '创建文档',
    'page.update': '更新文档',
    'page.delete': '删除文档',
    'page.restore': '恢复文档',
    'page.undelete': '恢复文档',
    'member.update': '修改成员权限',
    'member.remove': '移除成员',
    'invitation.create': '创建邀请',
    'import.complete': '完成导入',
    'workspace.create': '创建空间',
  };
  return (
    <div className="panel-view">
      <div className="panel-header">
        <div>
          <div className="section-eyebrow">EVERY CHANGE HAS A STORY</div>
          <h1>活动记录</h1>
          <p>记录空间里的重要变更，让团队协作有迹可循。</p>
        </div>
        <button className="button" onClick={() => query.refetch()}>
          <RefreshCw size={13} />
          刷新记录
        </button>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.length ? (
        <table className="table">
          <thead>
            <tr>
              <th>事件</th>
              <th>时间</th>
              <th>对象</th>
            </tr>
          </thead>
          <tbody>
            {query.data.map((event) => (
              <tr key={event.id}>
                <td>
                  <ShieldCheck
                    size={13}
                    style={{ display: 'inline', verticalAlign: 'middle', marginRight: 7 }}
                  />
                  {actionLabels[event.action] || event.action}
                </td>
                <td>{date(event.created_at, true)}</td>
                <td
                  style={{ fontSize: 9, color: '#a5b196', maxWidth: 180, overflowWrap: 'anywhere' }}
                >
                  {event.page_id || event.entity_id || '空间管理'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty
          title="新的篇章，即将开始"
          description="创建文档、调整权限和导入知识后，重要的空间活动会出现在这里。"
        />
      )}
    </div>
  );
}
