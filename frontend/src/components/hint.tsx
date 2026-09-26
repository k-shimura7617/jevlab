// 長い説明は画面に並べず「？」の title に入れる（判断に要るときだけ使う）
export function Hint({ text }: { text: string }) {
  return (
    <span className="hint" title={text} aria-label={text} tabIndex={0}>
      ？
    </span>
  )
}
