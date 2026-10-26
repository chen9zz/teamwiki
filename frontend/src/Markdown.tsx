import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link } from 'react-router-dom';
import { wikiMarkdown } from './lib';
import type { Page, PageLink } from './types';
export function Markdown({
  content,
  pages = [],
  links = [],
  wid = '',
}: {
  content: string;
  pages?: Page[];
  links?: PageLink[];
  wid?: string;
}) {
  return (
    <div className="prose">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children, node }) => (
            <h1 id={node?.position ? `heading-${node.position.start.line}` : undefined}>
              {children}
            </h1>
          ),
          h2: ({ children, node }) => (
            <h2 id={node?.position ? `heading-${node.position.start.line}` : undefined}>
              {children}
            </h2>
          ),
          h3: ({ children, node }) => (
            <h3 id={node?.position ? `heading-${node.position.start.line}` : undefined}>
              {children}
            </h3>
          ),
          h4: ({ children, node }) => (
            <h4 id={node?.position ? `heading-${node.position.start.line}` : undefined}>
              {children}
            </h4>
          ),
          h5: ({ children, node }) => (
            <h5 id={node?.position ? `heading-${node.position.start.line}` : undefined}>
              {children}
            </h5>
          ),
          h6: ({ children, node }) => (
            <h6 id={node?.position ? `heading-${node.position.start.line}` : undefined}>
              {children}
            </h6>
          ),
          a: ({ href, children }) =>
            href?.startsWith('/w/') ? (
              <Link to={href}>{children}</Link>
            ) : (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ),
          img: ({ src, alt }) => <img src={src} alt={alt || ''} loading="lazy" />,
        }}
      >
        {wikiMarkdown(content, pages, links, wid)}
      </ReactMarkdown>
    </div>
  );
}
