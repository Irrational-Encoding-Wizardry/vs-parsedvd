{
    package_name = "vsparsedvd";
    description = 
        let 
        content = builtins.readFile ''./${package_name}/_metadata.py'';
        version = builtins.match ''^"""(.*)"""'' content;
        in
        builtins.elemAt version 0;

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }: flake-utils.lib.eachDefaultSystem (system: 
    let
      pkgs = import nixpkgs {
        inherit system;
      };

      ## Change this to update python versions.
      ## run "nix flake lock --update-input nixpkgs" after you did that.
      python = pkgs.python310;

      # Fix brokenness of nix-VapourSynth on darwin
      vapoursynth = pkgs.vapoursynth.overrideAttrs (old: {
        patches = [];
        meta.broken = false;
        meta.platforms = [ system ];
      });

      # The python package for the python version of choice.
      vapoursynth_python = python.pkgs.buildPythonPackage {
        pname = "vapoursynth";
        inherit (pkgs.vapoursynth) version src;
        nativeBuildInputs = [ 
          python.pkgs.cython 
        ];
        buildInputs = [
          vapoursynth
        ];
      };
    in
    {
      devShells.default = pkgs.mkShell {
        buildInputs = [
          (python.withPackages (ps: [
            vapoursynth_python
          ]))
        ];
      };
      devShell = self.devShell.${system}.default;

      packages.default = python.pkgs.buildPythonPackage {
        pname = package_name;
        version = 
          let 
            content = builtins.readFile ''./${package_name}/_metadata.py'';
            version = builtins.match ".*__version__.*'(.*)'.*" content;
          in
          builtins.elemAt version 0;
        src = ./.;
        buildInputs = [
          vapoursynth_python 
        ];
      };

      packages.dist = 
        let
          build_python = python.withPackages (ps: [
            ps.setuptools
            ps.wheel
          ]);
        in
        pkgs.runCommandNoCC "${package_name}-dist" { src = ./.; } ''
          # Make sure the package test run.
          echo ${self.packages.${system}.default} >/dev/null
          cp -r $src/* .
          ${build_python}/bin/python setup.py bdist_wheel
          ${build_python}/bin/python setup.py sdist
          mkdir $out
          cp ./dist/* $out
        '';
      defaultPackage = self.packages.${system}.default;
    }
  );
}