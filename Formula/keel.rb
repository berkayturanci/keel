class Keel < Formula
  include Language::Python::Virtualenv

  desc "Project-neutral, multi-agent workflow core and autonomous issue shipping backbone"
  homepage "https://github.com/berkayturanci/keel"
  url "https://github.com/berkayturanci/keel/archive/refs/tags/v1.8.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"
  head "https://github.com/berkayturanci/keel.git", branch: "main"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "keel", shell_output("#{bin}/keel version")
  end
end
