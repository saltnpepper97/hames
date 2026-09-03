import { Dynamic } from "solid-js/web";
import { For } from "solid-js";
import type { Session } from "../../api/types";
import { useWebPlugins } from "../../shell/pluginContext";
import type { ComposerControlSeat } from "../../shell/plugins";

interface ComposerSeatProps {
  seat: ComposerControlSeat;
  session: Session;
  disabled: boolean;
  onSessionUpdated: (session: Session) => void;
  onError: (message: string) => void;
}

export function ComposerSeat(props: ComposerSeatProps) {
  const plugins = useWebPlugins();
  const controls = () => plugins.composerControls.get(props.seat) ?? [];

  return (
    <div class="composer-control-seat" data-seat={props.seat}>
      <For each={controls()}>
        {(control) => (
          <Dynamic
            component={control.component}
            session={props.session}
            disabled={props.disabled}
            onSessionUpdated={props.onSessionUpdated}
            onError={props.onError}
          />
        )}
      </For>
    </div>
  );
}
