import java.nio.file.Path;
import org.eclipse.jgit.api.Git;
import org.eclipse.jgit.storage.file.FileRepositoryBuilder;
import org.eclipse.jgit.util.Trace2;

/** Short-lived verification JVM; do not close Trace2 per job in Eclipse. */
public class DemoCheckout {
    public static void main(String[] args) throws Exception {
        int code = 1;
        try (var repo = new FileRepositoryBuilder()
                .setGitDir(Path.of(args[0], ".git").toFile()).build();
                var git = Git.wrap(repo)) {
            git.checkout().setName(args[1]).call();
            code = 0;
        } finally {
            Trace2.getInstance().exit(code);
            Trace2.getInstance().close();
        }
    }
}
