import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import cytoscape from 'cytoscape';
import { Network, GitBranch } from 'lucide-react';
import { api } from './api';
import type { GraphData } from './types';
import { Empty, ErrorNotice, Loading } from './ui';

export default function GraphView({ wid }: { wid: string }) {
  const query = useQuery({
    queryKey: ['graph', wid],
    queryFn: () => api<GraphData>(`/workspaces/${wid}/graph`),
  });
  const frame = useRef<HTMLDivElement>(null);
  const graph = useRef<cytoscape.Core | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    if (!frame.current || !query.data?.nodes.length) return;
    const data = query.data;
    const cy = cytoscape({
      container: frame.current,
      elements: [
        ...data.nodes.map((node) => ({ data: { id: node.id, label: node.title } })),
        ...data.edges.map((edge, index) => ({
          data: { id: `edge-${index}`, source: edge.source, target: edge.target },
        })),
      ],
      layout: { name: data.edges.length ? 'cose' : 'circle', animate: false, padding: 70 },
      minZoom: 0.25,
      maxZoom: 2.5,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': '#79a78d',
            'border-color': '#f7fbf4',
            'border-width': 4,
            width: 39,
            height: 39,
            label: 'data(label)',
            'font-size': 11,
            'font-family': 'DM Sans, Noto Sans SC, sans-serif',
            color: '#5e7a4e',
            'text-valign': 'bottom',
            'text-margin-y': 11,
            'text-max-width': '140px',
            'text-wrap': 'ellipsis',
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.4,
            'line-color': '#b4cbb5',
            'target-arrow-color': '#a7bfa9',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.75,
            'curve-style': 'bezier',
            opacity: 0.75,
          },
        },
        {
          selector: 'node:selected',
          style: { 'background-color': '#2e7960', 'border-color': '#b8d7ad', 'border-width': 6 },
        },
      ],
    });
    cy.on('tap', 'node', (event) => navigate(`/w/${wid}/p/${event.target.id()}`));
    graph.current = cy;
    const observer = new ResizeObserver(() => {
      cy.resize();
    });
    observer.observe(frame.current);
    return () => {
      observer.disconnect();
      cy.destroy();
      graph.current = null;
    };
  }, [query.data, wid, navigate]);
  return (
    <div className="panel-view">
      <div className="panel-header">
        <div>
          <div className="section-eyebrow">CONNECTED KNOWLEDGE</div>
          <h1>知识图谱</h1>
          <p>每一篇文档都是一个想法，每一次引用都让知识更完整。</p>
        </div>
        <button className="button" onClick={() => graph.current?.fit(undefined, 60)}>
          <Network size={13} />
          适应画布
        </button>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.nodes.length ? (
        <>
          <div className="graph-frame">
            <div
              ref={frame}
              style={{ height: '100%', width: '100%' }}
              role="img"
              aria-label={`知识图谱：${query.data.nodes.length} 篇文档，${query.data.edges.length} 个链接`}
            />
            <div className="graph-help">拖动平移 · 滚动缩放 · 点击节点打开文档</div>
          </div>
          <div className="graph-legend">
            <span>
              <i />
              {query.data.nodes.length} 篇文档
            </span>
            <span>
              <GitBranch size={12} />
              {query.data.edges.length} 条连接
            </span>
          </div>
          <div
            style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 20 }}
            aria-label="图谱中的文档"
          >
            {query.data.nodes.map((node) => (
              <button
                key={node.id}
                className="tag"
                onClick={() => navigate(`/w/${wid}/p/${node.id}`)}
              >
                {node.title} ↗
              </button>
            ))}
          </div>
        </>
      ) : (
        <Empty
          title="一个想法，连接更多可能"
          description="创建文档，并用 [[文档标题]] 建立引用关系，你的知识网络就会在这里生长。"
        />
      )}
    </div>
  );
}
