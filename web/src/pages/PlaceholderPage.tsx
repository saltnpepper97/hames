interface PlaceholderPageProps {
  eyebrow: string;
  title: string;
  description: string;
  detail: string;
}

export function PlaceholderPage(props: PlaceholderPageProps) {
  return (
    <section class="page placeholder-page" aria-labelledby="placeholder-title">
      <div class="page-heading">
        <div>
          <span class="eyebrow">{props.eyebrow}</span>
          <h1 id="placeholder-title">{props.title}</h1>
          <p>{props.description}</p>
        </div>
      </div>
      <div class="foundation-panel">
        <span class="foundation-rule" aria-hidden="true" />
        <div>
          <span class="eyebrow">Foundation ready</span>
          <h2>This surface is intentionally quiet for now.</h2>
          <p>{props.detail}</p>
        </div>
      </div>
    </section>
  );
}
