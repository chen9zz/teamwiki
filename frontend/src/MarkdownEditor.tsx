import CodeMirror from '@uiw/react-codemirror';
import { markdown } from '@codemirror/lang-markdown';
const extensions = [markdown()];
export default function MarkdownEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <CodeMirror
      value={value}
      extensions={extensions}
      onChange={onChange}
      placeholder="写下值得分享的知识…"
      basicSetup={{ lineNumbers: true, foldGutter: false, highlightActiveLineGutter: false }}
      minHeight="420px"
      aria-label="Markdown 文档内容"
    />
  );
}
