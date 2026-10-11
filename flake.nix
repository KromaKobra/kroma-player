{
  description = "Kroma Player — development shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # requirements.txt, resolved from nixpkgs instead of pip. Building the
        # wheels against the store means PySide6 finds its Qt plugins and QML
        # modules without a wrapper; pip wheels would need nix-ld to run at all.
        pythonEnv = pkgs.python313.withPackages (ps: [
          ps.pyside6      # requirements: >= 6.7
          ps.sqlalchemy   # >= 2.0
          ps.mutagen      # >= 1.47
          ps.pillow       # >= 12.2, with AVIF
          ps.syncedlyrics # >= 1.0.1
          ps.pytest
          # `pytest -n auto --dist loadfile` — the suite is mostly GUI tests
          # pumping a real event loop, which is wall time rather than CPU, so
          # it parallelises almost perfectly: 261s to 59s on eight cores.
          # `loadfile` keeps each file on one worker, which the module-scoped
          # window fixtures need — split a file across workers and every test
          # in it builds its own engine.
          ps.pytest-xdist
        ]);

        # Qt discovers plugins and QML modules through env vars, and on this
        # host those are already set system-wide by the KDE profile — so a bare
        # shell silently borrows whatever Qt the system happens to ship. That
        # is how SVG icons came up blank: qtbase carries png/jpeg/gif but not
        # libqsvg.so, and nothing else supplied it. Point both vars at the
        # modules pinned here, ahead of the system ones, so the shell resolves
        # against its own Qt rather than varying with what KDE is built from.
        qtModules = with pkgs.qt6; [
          qtbase
          qtdeclarative
          qtsvg         # libqsvg.so — Nocturne draws every icon as an SVG data URI
          qtmultimedia  # ffmpeg/gstreamer playback backends
        ];
        qtPluginPath = pkgs.lib.makeSearchPath "lib/qt-6/plugins" qtModules;
        qmlImportPath = pkgs.lib.makeSearchPath "lib/qt-6/qml" qtModules;

        # rcc compiles gui/<name>/ into the .kpi bundle the app actually loads
        # (scripts/build-gui, and the automatic rebuild in app/gui/installer.py).
        # It is a Qt build tool rather than a runtime library, so nothing above
        # brings it in: qtbase keeps it in libexec/, which is on nobody's PATH.
        qtTools = "${pkgs.qt6.qtbase}/libexec";
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.ffmpeg   # provides ffprobe too — audio conversion
            # Nothing online lives here: search and fetching are plugins, and
            # the programs a plugin needs come with that plugin's own shell
            # (kroma-fetch-audio's flake has the ones it runs).
          ];

          shellHook = ''
            export QT_PLUGIN_PATH="${qtPluginPath}''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
            export QML2_IMPORT_PATH="${qmlImportPath}''${QML2_IMPORT_PATH:+:$QML2_IMPORT_PATH}"
            export PATH="${qtTools}''${PATH:+:$PATH}"

            # direnv re-evaluates this on every cd into the tree, so the banner
            # is only worth printing for an explicit `nix develop`.
            if [ -z "$DIRENV_IN_ENVRC" ]; then
              echo "Kroma Player dev shell — python $(python --version | cut -d' ' -f2)"
              echo "  run:   python run.py"
              echo "  tests: pytest"
            fi
          '';
        };
      });
}
