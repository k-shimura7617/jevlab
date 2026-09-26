// 複数の文からなる説明は、句点（。）のあとで改行して読みやすくする
export function Sentences({ text }: { text: string }) {
  // 閉じかっこが続くときは、その後ろで区切る
  const parts = text.split(/(?<=。[）」』]*)/).map((s) => s.trim()).filter(Boolean)
  return (
    <>
      {parts.map((s, i) => (
        <span key={i} className="sentence">
          {s}
        </span>
      ))}
    </>
  )
}
