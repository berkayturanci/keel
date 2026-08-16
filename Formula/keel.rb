class Keel < Formula
  include Language::Python::Virtualenv

  desc "Project-neutral, multi-agent workflow core and autonomous issue shipping backbone"
  homepage "https://github.com/berkayturanci/keel"
  url "https://github.com/berkayturanci/keel/archive/refs/tags/v1.15.0.tar.gz"
  sha256 "c8a89d1f49887ab80b3ec8c8e8e2656541a8d59ae1bbe2ff6d007824e9cc9adc"
  license "Apache-2.0"
  head "https://github.com/berkayturanci/keel.git", branch: "main"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "keel", shell_output("#{bin}/keel version")
  end
end
