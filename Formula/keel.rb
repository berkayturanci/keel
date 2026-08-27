class Keel < Formula
  include Language::Python::Virtualenv

  desc "Project-neutral, multi-agent workflow core and autonomous issue shipping backbone"
  homepage "https://github.com/berkayturanci/keel"
  url "https://github.com/berkayturanci/keel/archive/refs/tags/v1.19.2.tar.gz"
  sha256 "726f1bf11bd58f512b0e1bfbcfe99c72a5190681c852859496ec748000c1c444"
  license "Apache-2.0"
  head "https://github.com/berkayturanci/keel.git", branch: "main"

  depends_on "python@3.12"

  # Homebrew installs Python packages with `--no-deps --no-binary=:all:`, so every
  # runtime dependency must be vendored here as an sdist — pip never resolves them
  # from PyPI. Without this stanza the virtualenv holds keel and nothing else, and
  # every command dies on `import yaml` before it prints anything (#787).
  #
  # `tzdata` is deliberately absent: pyproject marks it `sys_platform == 'win32'`,
  # and Homebrew does not run there.
  resource "PyYAML" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "keel", shell_output("#{bin}/keel version")
  end
end
